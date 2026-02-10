"""SSH-based I2C controller for maxTouch T100 touch sensor registers.

Connects to the SOM via asyncssh and executes smbus2 commands to read/write
maxTouch T100 registers over I2C (bus 1, address 0x4A).
"""

import asyncio
import json
import os
import asyncssh
from dataclasses import dataclass
from typing import Optional


# maxTouch I2C constants
I2C_BUS = 1
MAXTOUCH_I2C_ADDR = 0x4A
T100_BASE_ADDR = 1619  # 0x0653


@dataclass
class RegisterDef:
	"""Definition of a T100 register."""
	name: str
	offset: int       # offset from T100 base
	size: int          # 1 = 8-bit, 2 = 16-bit LE
	default: int
	min_val: int = 0
	max_val: int = 255

	@property
	def address(self) -> int:
		return T100_BASE_ADDR + self.offset

	@property
	def addr_bytes(self) -> tuple:
		"""I2C address as (lo, hi) bytes."""
		addr = self.address
		return (addr & 0xFF, (addr >> 8) & 0xFF)


# All tunable T100 touch registers
REGISTERS = {
	"TCHTHR": RegisterDef("TCHTHR", offset=30, size=1, default=40, min_val=0, max_val=255),
	"TCHHYST": RegisterDef("TCHHYST", offset=31, size=1, default=20, min_val=0, max_val=255),
	"MOVHYSTI": RegisterDef("MOVHYSTI", offset=47, size=2, default=50, min_val=0, max_val=4095),
	"MOVHYSTN": RegisterDef("MOVHYSTN", offset=49, size=2, default=10, min_val=0, max_val=4095),
	"AMPLHYST": RegisterDef("AMPLHYST", offset=51, size=1, default=0, min_val=0, max_val=255),
}


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

	async def read_register(self, name: str) -> int:
		"""Read a single register by name."""
		reg = REGISTERS.get(name)
		if not reg:
			raise ValueError(f"Unknown register: {name}")
		script = _build_read_script(reg)
		raw = await self._exec(script)
		return _parse_read_result(raw, reg)

	async def write_register(self, name: str, value: int) -> None:
		"""Write a single register by name."""
		reg = REGISTERS.get(name)
		if not reg:
			raise ValueError(f"Unknown register: {name}")
		value = max(reg.min_val, min(reg.max_val, value))
		script = _build_write_script(reg, value)
		await self._exec(script)
		print(f"[I2C] Wrote {name}={value}")

	async def read_all(self) -> dict:
		"""Read all registers and return as dict."""
		results = {}
		for name, reg in REGISTERS.items():
			try:
				results[name] = await self.read_register(name)
			except Exception as e:
				results[name] = {"error": str(e)}
		return results

	async def restore_defaults(self) -> dict:
		"""Reset all registers to factory defaults."""
		results = {}
		for name, reg in REGISTERS.items():
			try:
				await self.write_register(name, reg.default)
				results[name] = reg.default
			except Exception as e:
				results[name] = {"error": str(e)}
		return results

	def get_register_info(self) -> dict:
		"""Return register metadata for the frontend."""
		return {
			name: {
				"default": reg.default,
				"min": reg.min_val,
				"max": reg.max_val,
				"size": reg.size,
				"address": reg.address,
			}
			for name, reg in REGISTERS.items()
		}
