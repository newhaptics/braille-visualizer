"""SSH-based I2C controller for maxTouch touch sensor registers.

Connects to the SOM via asyncssh and executes smbus2 commands to read/write
maxTouch registers over I2C (bus 1, address 0x4A).

Supports T100 (Multi-Touch), T42 (Touch Suppression), T8 (Acquisition),
and T40 (Grip Suppression) objects.
"""

import asyncio
import json
import os
import asyncssh
from dataclasses import dataclass, field
from typing import Optional


# maxTouch I2C constants
I2C_BUS = 1
MAXTOUCH_I2C_ADDR = 0x4A

# Object base addresses (from device object table)
T8_BASE_ADDR = 1007   # GEN_ACQUISITIONCONFIG
T40_BASE_ADDR = 1058  # PROCI_GRIPSUPPRESSION
T42_BASE_ADDR = 1065  # PROCI_TOUCHSUPPRESSION
T100_BASE_ADDR = 1619 # TOUCH_MULTITOUCHSCREEN


@dataclass
class RegisterDef:
	"""Definition of a maxTouch register."""
	name: str
	offset: int        # offset from object base
	size: int          # 1 = 8-bit, 2 = 16-bit LE
	default: int
	base_addr: int     # object base address
	min_val: int = 0
	max_val: int = 255
	description: str = ""

	@property
	def address(self) -> int:
		return self.base_addr + self.offset

	@property
	def addr_bytes(self) -> tuple:
		"""I2C address as (lo, hi) bytes."""
		addr = self.address
		return (addr & 0xFF, (addr >> 8) & 0xFF)


# ── Register Definitions ──────────────────────────────────────────────────────

REGISTERS = {
	"T100": {
		"TCHTHR": RegisterDef("TCHTHR", offset=30, size=1, default=40, base_addr=T100_BASE_ADDR,
			description="Touch detection threshold. Higher = less sensitive. Typical: 30-80."),
		"TCHHYST": RegisterDef("TCHHYST", offset=31, size=1, default=20, base_addr=T100_BASE_ADDR,
			description="Touch detection hysteresis. Recommended: <=25% of TCHTHR."),
		"INTTHR": RegisterDef("INTTHR", offset=32, size=1, default=20, base_addr=T100_BASE_ADDR,
			description="Internal touch tracking threshold. 0 = (TCHTHR-TCHHYST)/2."),
		"INTTHRHYST": RegisterDef("INTTHRHYST", offset=53, size=1, default=4, base_addr=T100_BASE_ADDR,
			description="Internal tracking threshold hysteresis. Capped to 75% of INTTHR."),
		"TCHDIDOWN": RegisterDef("TCHDIDOWN", offset=39, size=1, default=2, base_addr=T100_BASE_ADDR,
			description="Touch detect integration down. Debounce frames before reporting. Higher = more filtering."),
		"TCHDIUP": RegisterDef("TCHDIUP", offset=40, size=1, default=2, base_addr=T100_BASE_ADDR,
			description="Touch detect integration up. Debounce frames for release detection."),
		"NEXTTCHDI": RegisterDef("NEXTTCHDI", offset=41, size=1, default=2, base_addr=T100_BASE_ADDR,
			description="Next touch detect integration. Additional DI for subsequent touches."),
		"MOVHYSTI": RegisterDef("MOVHYSTI", offset=47, size=2, default=50, base_addr=T100_BASE_ADDR, max_val=4095,
			description="Initial movement hysteresis. Touch must move this far before position updates begin."),
		"MOVHYSTN": RegisterDef("MOVHYSTN", offset=49, size=2, default=10, base_addr=T100_BASE_ADDR, max_val=4095,
			description="Next movement hysteresis. Jitter filter for ongoing touches. Internally reduced while moving."),
		"AMPLHYST": RegisterDef("AMPLHYST", offset=51, size=1, default=0, base_addr=T100_BASE_ADDR,
			description="Amplitude change hysteresis. Amplitude must change by this much before reporting. 0 = report every change."),
		"XEDGECFG": RegisterDef("XEDGECFG", offset=15, size=1, default=0, base_addr=T100_BASE_ADDR, max_val=63,
			description="X edge correction gradient. 0 = disabled. Typical: 9."),
		"XEDGEDIST": RegisterDef("XEDGEDIST", offset=16, size=1, default=0, base_addr=T100_BASE_ADDR,
			description="X edge correction distance. Half typical touch diameter in 10-bit pixels."),
		"YEDGECFG": RegisterDef("YEDGECFG", offset=26, size=1, default=0, base_addr=T100_BASE_ADDR, max_val=63,
			description="Y edge correction gradient. 0 = disabled. Typical: 9."),
		"YEDGEDIST": RegisterDef("YEDGEDIST", offset=27, size=1, default=0, base_addr=T100_BASE_ADDR,
			description="Y edge correction distance. Half typical touch diameter in 10-bit pixels."),
	},

	"T42": {
		"CTRL": RegisterDef("CTRL", offset=0, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Touch suppression control. Bit 0=Enable, 1=EdgeSup, 2=ShapeSup, 3=SupDistEn, 4=DistLock."),
		"MAXAPPRAREA": RegisterDef("MAXAPPRAREA", offset=2, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Max approach area threshold. 0 = 40 channels. Channels above (INTTHR-INTTHRHYST)."),
		"MAXTCHAREA": RegisterDef("MAXTCHAREA", offset=3, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Max touch area threshold. 0 = 35 channels. Channels above (TCHTHR-TCHHYST)."),
		"SUPSTRENGTH": RegisterDef("SUPSTRENGTH", offset=4, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Suppression strength. 0=normal(128), 1-127=more aggressive, 129-254=less, 255=disable edge+large obj."),
		"SUPEXTTO": RegisterDef("SUPEXTTO", offset=5, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Suppression extension timeout. Cycles before ambiguous touch is suppressed. 0 = never expire."),
		"MAXNUMTCHS": RegisterDef("MAXNUMTCHS", offset=6, size=1, default=0, base_addr=T42_BASE_ADDR, max_val=15,
			description="Max number of touches. If exceeded, all suppressed. 0 = no limit."),
		"SHAPESTRENGTH": RegisterDef("SHAPESTRENGTH", offset=7, size=1, default=0, base_addr=T42_BASE_ADDR, max_val=31,
			description="Shape-based rejection strength. 0=10(default), 1-9=more aggressive, 11-31=less."),
		"SUPDIST": RegisterDef("SUPDIST", offset=8, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Distance suppression radius in nodes. 0 = 5 nodes. Touches within this of a suppressed object are also suppressed."),
		"DISTHYST": RegisterDef("DISTHYST", offset=9, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Distance suppression hysteresis in nodes."),
		"MAXSCRNAREA": RegisterDef("MAXSCRNAREA", offset=10, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Max screen area. Total nodes in detect = value x 4. If exceeded, full screen suppression. 0 = disabled."),
		"EDGESUPSTRENGTH": RegisterDef("EDGESUPSTRENGTH", offset=13, size=1, default=0, base_addr=T42_BASE_ADDR,
			description="Edge suppression strength. 0=73(normal). Higher=less aggressive. 255=disable edge classification."),
	},

	"T8": {
		"TCHAUTOCAL": RegisterDef("TCHAUTOCAL", offset=4, size=1, default=0, base_addr=T8_BASE_ADDR,
			description="Auto-recalibration after prolonged static touch. 200ms units. 0 = disabled."),
		"ATCHCALST": RegisterDef("ATCHCALST", offset=6, size=1, default=0, base_addr=T8_BASE_ADDR,
			description="Anti-touch calibration suspend time. 200ms units. 0 = immediate. 255 = disable finger recovery."),
		"ATCHCALSTHR": RegisterDef("ATCHCALSTHR", offset=7, size=1, default=0, base_addr=T8_BASE_ADDR,
			description="Anti-touch calibration suspend threshold. Channels above this suspend recovery. 0 = never suspend."),
		"ATCHFRCCALTHR": RegisterDef("ATCHFRCCALTHR", offset=8, size=1, default=50, base_addr=T8_BASE_ADDR,
			description="Anti-touch forced calibration threshold. If touch+antitouch channels >= this, checks ratio. 0 = disabled."),
		"ATCHFRCCALRATIO": RegisterDef("ATCHFRCCALRATIO", offset=9, size=1, default=25, base_addr=T8_BASE_ADDR,
			description="Anti-touch forced calibration ratio. Ratio of antitouch to total. Typical: 25 (60%)."),
	},

	"T40": {
		"CTRL": RegisterDef("CTRL", offset=0, size=1, default=0, base_addr=T40_BASE_ADDR,
			description="Grip suppression control. Bit 0 = Enable."),
		"XLOGRIP": RegisterDef("XLOGRIP", offset=1, size=1, default=0, base_addr=T40_BASE_ADDR,
			description="X low grip suppression boundary."),
		"XHIGRIP": RegisterDef("XHIGRIP", offset=2, size=1, default=0, base_addr=T40_BASE_ADDR,
			description="X high grip suppression boundary."),
		"YLOGRIP": RegisterDef("YLOGRIP", offset=3, size=1, default=0, base_addr=T40_BASE_ADDR,
			description="Y low grip suppression boundary."),
		"YHIGRIP": RegisterDef("YHIGRIP", offset=4, size=1, default=0, base_addr=T40_BASE_ADDR,
			description="Y high grip suppression boundary."),
	},
}

# Flat lookup for backward compatibility
REGISTERS_FLAT: dict[str, RegisterDef] = {}
for obj_regs in REGISTERS.values():
	for name, reg in obj_regs.items():
		# Prefix with object name if there's a collision (e.g., T42.CTRL vs T40.CTRL)
		REGISTERS_FLAT[name] = reg


def _build_read_script(reg: RegisterDef) -> str:
	"""Build a Python one-liner to read a register on the SOM."""
	lo, hi = reg.addr_bytes
	return (
		f"from smbus2 import SMBus, i2c_msg; "
		f"bus=SMBus({I2C_BUS}); "
		f"bus.i2c_rdwr(i2c_msg.write({MAXTOUCH_I2C_ADDR}, [{lo}, {hi}])); "
		f"r=i2c_msg.read({MAXTOUCH_I2C_ADDR}, {reg.size}); "
		f"bus.i2c_rdwr(r); "
		f"import json; print(json.dumps(list(r))); "
		f"bus.close()"
	)


def _build_write_script(reg: RegisterDef, value: int) -> str:
	"""Build a Python one-liner to write a register on the SOM."""
	lo, hi = reg.addr_bytes
	if reg.size == 1:
		data_bytes = f"{value & 0xFF}"
	else:
		# 16-bit little-endian
		data_bytes = f"{value & 0xFF}, {(value >> 8) & 0xFF}"
	return (
		f"from smbus2 import SMBus, i2c_msg; "
		f"bus=SMBus({I2C_BUS}); "
		f"bus.i2c_rdwr(i2c_msg.write({MAXTOUCH_I2C_ADDR}, [{lo}, {hi}, {data_bytes}])); "
		f"bus.close(); "
		f"print('ok')"
	)


def _parse_read_result(raw: str, reg: RegisterDef) -> int:
	"""Parse the JSON list output from a register read into an integer value."""
	vals = json.loads(raw.strip())
	if reg.size == 1:
		return vals[0]
	else:
		return vals[0] | (vals[1] << 8)


class I2CController:
	"""Manages SSH connection to SOM for I2C register access."""

	def __init__(self):
		self._conn: Optional[asyncssh.SSHClientConnection] = None
		self._host: Optional[str] = None
		self._lock = asyncio.Lock()

	@property
	def connected(self) -> bool:
		return self._conn is not None and not self._conn.is_closed()

	@property
	def host(self) -> Optional[str]:
		return self._host

	async def connect(self, host: str, username: str = "root", port: int = 22) -> None:
		"""Open SSH connection to the SOM."""
		if self.connected:
			await self.disconnect()

		# Find SSH key — check common locations
		key_paths = [
			os.path.expandvars(r"%USERPROFILE%\.ssh\id_ed25519"),
			r"C:\SPB_Data\.ssh\id_ed25519",
			os.path.expanduser("~/.ssh/id_ed25519"),
		]
		client_keys = []
		for p in key_paths:
			if os.path.exists(p):
				try:
					client_keys.append(asyncssh.read_private_key(p))
				except Exception:
					pass

		try:
			self._conn = await asyncssh.connect(
				host, port=port, username=username,
				known_hosts=None,  # Skip host key verification for dev
				client_keys=client_keys if client_keys else None,
				agent_path=None,  # Disable SSH agent to avoid interference
			)
			self._host = host
			print(f"[I2C] Connected to {host}")
		except Exception as e:
			self._conn = None
			self._host = None
			raise ConnectionError(f"Failed to connect to {host}: {e}")

	async def disconnect(self) -> None:
		"""Close SSH connection."""
		if self._conn:
			self._conn.close()
			await self._conn.wait_closed()
			self._conn = None
			self._host = None
			print("[I2C] Disconnected")

	async def _exec(self, script: str) -> str:
		"""Execute a Python script on the SOM via SSH."""
		if not self.connected:
			raise ConnectionError("Not connected to SOM")
		async with self._lock:
			result = await self._conn.run(f"python3.12 -c \"{script}\"", check=True)
			return result.stdout

	async def read_register(self, name: str, obj: str = None) -> int:
		"""Read a single register by name. If obj is specified, look in that object group."""
		reg = self._find_register(name, obj)
		script = _build_read_script(reg)
		raw = await self._exec(script)
		return _parse_read_result(raw, reg)

	async def write_register(self, name: str, value: int, obj: str = None) -> None:
		"""Write a single register by name."""
		reg = self._find_register(name, obj)
		value = max(reg.min_val, min(reg.max_val, value))
		script = _build_write_script(reg, value)
		await self._exec(script)
		print(f"[I2C] Wrote {name}={value}")

	def _find_register(self, name: str, obj: str = None) -> RegisterDef:
		"""Find a register definition by name, optionally scoped to an object."""
		if obj:
			obj_regs = REGISTERS.get(obj)
			if not obj_regs:
				raise ValueError(f"Unknown object: {obj}")
			reg = obj_regs.get(name)
			if not reg:
				raise ValueError(f"Unknown register {name} in {obj}")
			return reg
		# Flat lookup — check each object group
		for obj_name, obj_regs in REGISTERS.items():
			if name in obj_regs:
				return obj_regs[name]
		raise ValueError(f"Unknown register: {name}")

	async def read_all(self) -> dict:
		"""Read all registers in a single SSH command (batched)."""
		lines = [
			"from smbus2 import SMBus, i2c_msg; import json",
			f"bus=SMBus({I2C_BUS}); r={{}}",
		]
		for obj, obj_regs in REGISTERS.items():
			lines.append(f"r['{obj}']={{}}")
			for name, reg in obj_regs.items():
				lo, hi = reg.addr_bytes
				lines.append(
					f"bus.i2c_rdwr(i2c_msg.write({MAXTOUCH_I2C_ADDR},[{lo},{hi}]));"
					f"v=i2c_msg.read({MAXTOUCH_I2C_ADDR},{reg.size});"
					f"bus.i2c_rdwr(v);d=list(v);"
					f"r['{obj}']['{name}']="
					+ (f"d[0]" if reg.size == 1 else f"d[0]|d[1]<<8")
				)
		lines.append("bus.close(); print(json.dumps(r))")
		script = "; ".join(lines)
		raw = await self._exec(script)
		return json.loads(raw.strip())

	async def write_all(self, config: dict) -> dict:
		"""Write registers in a single SSH command (batched)."""
		lines = [
			"from smbus2 import SMBus, i2c_msg; import json",
			f"bus=SMBus({I2C_BUS}); r={{}}",
		]
		for obj, values in config.items():
			if obj not in REGISTERS or not isinstance(values, dict):
				continue
			obj_regs = REGISTERS[obj]
			lines.append(f"r['{obj}']={{}}")
			for name, value in values.items():
				if name not in obj_regs:
					continue
				reg = obj_regs[name]
				value = max(reg.min_val, min(reg.max_val, int(value)))
				lo, hi = reg.addr_bytes
				if reg.size == 1:
					data = f"{value & 0xFF}"
				else:
					data = f"{value & 0xFF},{(value >> 8) & 0xFF}"
				lines.append(
					f"bus.i2c_rdwr(i2c_msg.write({MAXTOUCH_I2C_ADDR},[{lo},{hi},{data}]));"
					f"r['{obj}']['{name}']={value}"
				)
		lines.append("bus.close(); print(json.dumps(r))")
		script = "; ".join(lines)
		raw = await self._exec(script)
		return json.loads(raw.strip())

	async def restore_defaults(self) -> dict:
		"""Reset all registers to factory defaults (batched)."""
		config = {}
		for obj, obj_regs in REGISTERS.items():
			config[obj] = {name: reg.default for name, reg in obj_regs.items()}
		return await self.write_all(config)

	async def read_xcfg_file(self) -> Optional[str]:
		"""Read the touchscreen xcfg file from the SOM."""
		if not self.connected:
			return None
		paths = [
			"/root/firmware/touchscreen.xcfg",
			"/usr/lib/firmware/codex/touchscreen-1.0.0.xcfg",
		]
		for path in paths:
			try:
				result = await self._conn.run(f"cat {path}", check=True)
				if result.stdout and result.stdout.strip():
					print(f"[I2C] Read xcfg from {path}")
					return result.stdout
			except Exception:
				continue
		# Try glob pattern for versioned files
		try:
			result = await self._conn.run(
				"ls /usr/lib/firmware/codex/touchscreen-*.xcfg 2>/dev/null | head -1",
				check=False
			)
			if result.stdout and result.stdout.strip():
				path = result.stdout.strip()
				result = await self._conn.run(f"cat {path}", check=True)
				if result.stdout:
					print(f"[I2C] Read xcfg from {path}")
					return result.stdout
		except Exception:
			pass
		print("[I2C] Could not read xcfg file from SOM")
		return None

	def get_register_info(self) -> dict:
		"""Return register metadata grouped by object for the frontend."""
		info = {}
		for obj, obj_regs in REGISTERS.items():
			info[obj] = {
				name: {
					"default": reg.default,
					"min": reg.min_val,
					"max": reg.max_val,
					"size": reg.size,
					"address": reg.address,
					"description": reg.description,
				}
				for name, reg in obj_regs.items()
			}
		return info
