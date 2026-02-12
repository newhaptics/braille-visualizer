"""Touch configuration profile management.

Handles save/load of touch register presets as JSON files,
plus built-in presets and xcfg file parsing.
"""

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ── xcfg section name → object key mapping ──
_XCFG_SECTIONS = {
	"TOUCH_MULTITOUCHSCREEN_T100": "T100",
	"PROCI_TOUCHSUPPRESSION_T42": "T42",
	"GEN_ACQUISITIONCONFIG_T8": "T8",
	"PROCI_GRIPSUPPRESSION_T40": "T40",
}

# Registers we care about per object (name → expected)
# Used to filter xcfg values to only the registers we manage
_MANAGED_REGISTERS = {
	"T100": {"TCHTHR", "TCHHYST", "INTTHR", "INTTHRHYST", "TCHDIDOWN", "TCHDIUP",
			 "NEXTTCHDI", "MOVHYSTI", "MOVHYSTN", "AMPLHYST",
			 "XEDGECFG", "XEDGEDIST", "YEDGECFG", "YEDGEDIST"},
	"T42": {"CTRL", "MAXAPPRAREA", "MAXTCHAREA", "SUPSTRENGTH", "SUPEXTTO",
			"MAXNUMTCHS", "SHAPESTRENGTH", "SUPDIST", "DISTHYST", "MAXSCRNAREA",
			"EDGESUPSTRENGTH"},
	"T8": {"TCHAUTOCAL", "ATCHCALST", "ATCHCALSTHR", "ATCHFRCCALTHR", "ATCHFRCCALRATIO"},
	"T40": {"CTRL", "XLOGRIP", "XHIGRIP", "YLOGRIP", "YHIGRIP"},
}


@dataclass
class TouchProfile:
	name: str
	description: str = ""
	tags: list = field(default_factory=list)
	created: str = ""
	modified: str = ""
	registers: dict = field(default_factory=dict)
	builtin: bool = False

	def to_dict(self) -> dict:
		d = asdict(self)
		del d["builtin"]  # Don't persist the builtin flag
		return d

	@classmethod
	def from_dict(cls, d: dict) -> "TouchProfile":
		return cls(
			name=d.get("name", ""),
			description=d.get("description", ""),
			tags=d.get("tags", []),
			created=d.get("created", ""),
			modified=d.get("modified", ""),
			registers=d.get("registers", {}),
		)


def get_profiles_dir() -> str:
	"""Return the profiles directory path, creating it if needed."""
	d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "touch_profiles")
	os.makedirs(d, exist_ok=True)
	return d


def _sanitize_filename(name: str) -> str:
	"""Convert a profile name to a safe filename."""
	safe = re.sub(r'[^\w\s-]', '', name.lower()).strip()
	safe = re.sub(r'[\s]+', '_', safe)
	return safe or "profile"


def list_profiles() -> list[dict]:
	"""List all saved profiles (summaries only)."""
	profiles_dir = get_profiles_dir()
	result = []
	for fname in sorted(os.listdir(profiles_dir)):
		if not fname.endswith(".json"):
			continue
		try:
			with open(os.path.join(profiles_dir, fname)) as f:
				d = json.load(f)
			result.append({
				"filename": fname,
				"name": d.get("name", fname),
				"description": d.get("description", ""),
				"tags": d.get("tags", []),
				"modified": d.get("modified", ""),
				"builtin": False,
			})
		except Exception:
			continue
	return result


def load_profile(filename: str) -> TouchProfile:
	"""Load a profile from file."""
	path = os.path.join(get_profiles_dir(), filename)
	with open(path) as f:
		d = json.load(f)
	return TouchProfile.from_dict(d)


def save_profile(profile: TouchProfile, filename: str = None) -> str:
	"""Save a profile to file. Returns the filename used."""
	now = datetime.now().isoformat(timespec="seconds")
	if not profile.created:
		profile.created = now
	profile.modified = now

	if filename is None:
		filename = _sanitize_filename(profile.name) + ".json"

	path = os.path.join(get_profiles_dir(), filename)
	with open(path, "w") as f:
		json.dump(profile.to_dict(), f, indent=2)
	return filename


def delete_profile(filename: str) -> bool:
	"""Delete a saved profile. Returns True if deleted."""
	path = os.path.join(get_profiles_dir(), filename)
	if os.path.exists(path):
		os.remove(path)
		return True
	return False


# ── xcfg parsing ──────────────────────────────────────────────────────────────

def parse_xcfg(text: str) -> dict:
	"""Parse a maxTouch .xcfg file and extract managed register values.

	Returns dict like {"T100": {"TCHTHR": 40, ...}, "T42": {...}, ...}
	"""
	result = {}
	current_obj = None
	# Match section headers like [TOUCH_MULTITOUCHSCREEN_T100 INSTANCE 0]
	section_re = re.compile(r'^\[(\w+)\s+INSTANCE\s+\d+\]')
	# Match register lines like "30 1 TCHTHR=40" or "47 2 MOVHYSTI=50"
	reg_re = re.compile(r'^\d+\s+\d+\s+(\w+)=(-?\d+)')

	for line in text.splitlines():
		line = line.strip()
		m = section_re.match(line)
		if m:
			section_name = m.group(1)
			current_obj = _XCFG_SECTIONS.get(section_name)
			continue

		if current_obj is None:
			continue

		m = reg_re.match(line)
		if m:
			reg_name = m.group(1)
			reg_value = int(m.group(2))
			managed = _MANAGED_REGISTERS.get(current_obj, set())
			if reg_name in managed:
				if current_obj not in result:
					result[current_obj] = {}
				result[current_obj][reg_name] = reg_value

	return result


# ── Built-in presets ──────────────────────────────────────────────────────────

# Hardcoded fallback defaults matching the xcfg shipped with the device
_HARDCODED_FACTORY = {
	"T100": {
		"TCHTHR": 40, "TCHHYST": 20, "INTTHR": 20, "INTTHRHYST": 4,
		"TCHDIDOWN": 2, "TCHDIUP": 2, "NEXTTCHDI": 2,
		"MOVHYSTI": 50, "MOVHYSTN": 10, "AMPLHYST": 0,
		"XEDGECFG": 0, "XEDGEDIST": 0, "YEDGECFG": 0, "YEDGEDIST": 0,
	},
	"T42": {
		"CTRL": 0, "MAXAPPRAREA": 0, "MAXTCHAREA": 0, "SUPSTRENGTH": 0,
		"SUPEXTTO": 0, "MAXNUMTCHS": 0, "SHAPESTRENGTH": 0,
		"SUPDIST": 0, "DISTHYST": 0, "MAXSCRNAREA": 0, "EDGESUPSTRENGTH": 0,
	},
	"T8": {
		"TCHAUTOCAL": 0, "ATCHCALST": 0, "ATCHCALSTHR": 0,
		"ATCHFRCCALTHR": 50, "ATCHFRCCALRATIO": 25,
	},
	"T40": {
		"CTRL": 0, "XLOGRIP": 0, "XHIGRIP": 0, "YLOGRIP": 0, "YHIGRIP": 0,
	},
}


def get_builtin_presets(xcfg_text: str = None) -> list[TouchProfile]:
	"""Return built-in presets. If xcfg_text is provided, use it for Factory Default."""

	# Factory Default — from xcfg or hardcoded fallback
	if xcfg_text:
		factory_regs = parse_xcfg(xcfg_text)
		# Fill in any missing registers from hardcoded defaults
		for obj, regs in _HARDCODED_FACTORY.items():
			if obj not in factory_regs:
				factory_regs[obj] = dict(regs)
			else:
				for name, val in regs.items():
					if name not in factory_regs[obj]:
						factory_regs[obj][name] = val
	else:
		factory_regs = _HARDCODED_FACTORY

	presets = [
		TouchProfile(
			name="Factory Default",
			description="Register values from the device's boot-time xcfg configuration.",
			tags=["default", "factory"],
			registers=factory_regs,
			builtin=True,
		),
		TouchProfile(
			name="Edge Suppression Active",
			description="Enables T42 touch suppression with edge suppression to filter phantom touches at sensor edges. Tightens TCHHYST to 25% of TCHTHR and increases TCHDIDOWN debounce.",
			tags=["phantom-fix", "edge", "suppression"],
			registers={
				"T100": {
					"TCHTHR": 40, "TCHHYST": 10, "INTTHR": 20, "INTTHRHYST": 4,
					"TCHDIDOWN": 4, "TCHDIUP": 2, "NEXTTCHDI": 2,
					"MOVHYSTI": 50, "MOVHYSTN": 10, "AMPLHYST": 0,
					"XEDGECFG": 0, "XEDGEDIST": 0, "YEDGECFG": 0, "YEDGEDIST": 0,
				},
				"T42": {
					"CTRL": 3, "MAXAPPRAREA": 0, "MAXTCHAREA": 0, "SUPSTRENGTH": 0,
					"SUPEXTTO": 5, "MAXNUMTCHS": 0, "SHAPESTRENGTH": 0,
					"SUPDIST": 0, "DISTHYST": 0, "MAXSCRNAREA": 0, "EDGESUPSTRENGTH": 73,
				},
				"T8": {
					"TCHAUTOCAL": 0, "ATCHCALST": 0, "ATCHCALSTHR": 0,
					"ATCHFRCCALTHR": 50, "ATCHFRCCALRATIO": 25,
				},
				"T40": {
					"CTRL": 0, "XLOGRIP": 0, "XHIGRIP": 0, "YLOGRIP": 0, "YHIGRIP": 0,
				},
			},
			builtin=True,
		),
		TouchProfile(
			name="Click Feel Mode",
			description="Minimizes movement hysteresis for continuous amplitude streaming. Used for force measurement calibration.",
			tags=["calibration", "force", "click-feel"],
			registers={
				"T100": {
					"TCHTHR": 40, "TCHHYST": 20, "INTTHR": 20, "INTTHRHYST": 4,
					"TCHDIDOWN": 2, "TCHDIUP": 2, "NEXTTCHDI": 2,
					"MOVHYSTI": 1, "MOVHYSTN": 1, "AMPLHYST": 0,
					"XEDGECFG": 0, "XEDGEDIST": 0, "YEDGECFG": 0, "YEDGEDIST": 0,
				},
				"T42": {
					"CTRL": 0, "MAXAPPRAREA": 0, "MAXTCHAREA": 0, "SUPSTRENGTH": 0,
					"SUPEXTTO": 0, "MAXNUMTCHS": 0, "SHAPESTRENGTH": 0,
					"SUPDIST": 0, "DISTHYST": 0, "MAXSCRNAREA": 0, "EDGESUPSTRENGTH": 0,
				},
				"T8": {
					"TCHAUTOCAL": 0, "ATCHCALST": 0, "ATCHCALSTHR": 0,
					"ATCHFRCCALTHR": 50, "ATCHFRCCALRATIO": 25,
				},
				"T40": {
					"CTRL": 0, "XLOGRIP": 0, "XHIGRIP": 0, "YLOGRIP": 0, "YHIGRIP": 0,
				},
			},
			builtin=True,
		),
		TouchProfile(
			name="High Sensitivity",
			description="Lower touch threshold and debounce for maximum sensitivity. Useful for testing light touches.",
			tags=["sensitive", "testing"],
			registers={
				"T100": {
					"TCHTHR": 25, "TCHHYST": 6, "INTTHR": 10, "INTTHRHYST": 2,
					"TCHDIDOWN": 1, "TCHDIUP": 1, "NEXTTCHDI": 1,
					"MOVHYSTI": 30, "MOVHYSTN": 5, "AMPLHYST": 0,
					"XEDGECFG": 0, "XEDGEDIST": 0, "YEDGECFG": 0, "YEDGEDIST": 0,
				},
				"T42": {
					"CTRL": 0, "MAXAPPRAREA": 0, "MAXTCHAREA": 0, "SUPSTRENGTH": 0,
					"SUPEXTTO": 0, "MAXNUMTCHS": 0, "SHAPESTRENGTH": 0,
					"SUPDIST": 0, "DISTHYST": 0, "MAXSCRNAREA": 0, "EDGESUPSTRENGTH": 0,
				},
				"T8": {
					"TCHAUTOCAL": 0, "ATCHCALST": 0, "ATCHCALSTHR": 0,
					"ATCHFRCCALTHR": 50, "ATCHFRCCALRATIO": 25,
				},
				"T40": {
					"CTRL": 0, "XLOGRIP": 0, "XHIGRIP": 0, "YLOGRIP": 0, "YHIGRIP": 0,
				},
			},
			builtin=True,
		),
	]
	return presets
