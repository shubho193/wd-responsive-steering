"""Responsive Steering - build script.

Rebuilds mod/responsive_steering/workspace/generated/databases/generic/*.lib
from the game's own patch.dat, applying the knobs below.

    python build.py "<Watch_Dogs folder>" [--gibbed "<Gibbed Tools folder>"]

Needs Gibbed Disrupt tools (default: <game>/hV_WD_ModdingKit_PLUS/Tools/Gibbed Tools).
Also writes editable XML to src/xml for compile.bat.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile

# ---------------------------------------------------------------- knobs ---
# Seconds for the front wheels to reach full lock from centre (linear ramp).
# Vanilla is 0.4 - 1.9 s. Lower = twitchier taps; a 0.2 s tap gets 0.2/this of lock.
CAR_FULL_LOCK_TIME = 0.70
HEAVY_FULL_LOCK_TIME = 0.85      # trucks, vans with "Heavy" profiles, ambulance
# Return-to-centre / counter-steer is this much faster than turn-in, so a tap
# springs back quickly instead of leaving the wheels turned.
COUNTER_STEER_MULT = 2.0
# Vanilla caps the wheel angle to the tyre's peak-slip angle (selPeakSlipLoadMode),
# so holding a direction at speed never gets past a small angle. True lets a
# held input reach the car's full fMaxAngle.
FORCE_MAX_ANGLE = True
# Speed-sensitive max angle (the MotorcycleMaxSteerAngle block bikes use; switched
# on for cars here). Full fMaxAngle up to SPEED_LOW, tapering linearly to
# ANGLE_AT_HIGH degrees at SPEED_HIGH and above. Speeds in m/s (x3.6 = km/h).
# None = off (a held input reaches full lock at any speed).
SPEED_SENSITIVE_STEER = True
SPEED_LOW = 8.0                  # ~30 km/h
SPEED_HIGH = 35.0                # ~125 km/h
ANGLE_AT_HIGH = 10.0             # degrees of wheel angle at SPEED_HIGH
LOW_SPEED_ANGLE_SCALE = 0.85     # fraction of fMaxAngle used below SPEED_LOW (1.0 = full lock)
# Chassis yaw inertia (how sluggishly the body rotates). Vanilla 1.0 - 1.6.
# 1.0 = physically "real" inertia; lower = more arcade-like rotation.
CAR_YAW_INERTIA = 1.2
HEAVY_YAW_INERTIA = 1.1          # None = leave heavy vehicles as vanilla

# --- grip (vehicleaxle.lib: per-axle multipliers on the tyre model, vanilla 1.0)
# More rear than front = stable, less snap-oversteer / spinning out.
# Rear > front = understeer (front washes out at speed); keep them about equal.
FRONT_LATERAL_GRIP = 1.60
REAR_LATERAL_GRIP = 1.90
LONGITUDINAL_GRIP = 1.15         # traction under throttle / braking, both axles
# --- tyre response (vehiclemilliken.lib: how fast cornering force builds up)
LATERAL_STIFFNESS_MULT = 1.15
# Extra stiffness on rear-only tyre entries (names ending .rear) so the rear
# tracks behind the front instead of sliding with it (car pivots near the rear
# axle, not its middle). Shared front/rear tyre entries can't be split.
REAR_STIFFNESS_EXTRA = 1.35
# Tyre friction drops as load rises (vanilla 0.25 - 1.0). Lower = the loaded
# outside tyre keeps its grip in fast corners.
MAX_FRICTION_LOAD_DEPENDENCY = 0.25

# --- downforce (vehicleaerodynamics.lib fLiftCdaFront, m^2; negative = pushes
# the car down). Vanilla 0 to +0.5 (slight lift). -2.0 adds about 2 kN of load
# at 150 km/h (~+15% grip on a 1.5 t car), growing with speed squared.
# None = leave vanilla.
DOWNFORCE_CDA = -2.0
DOWNFORCE_FRONT_SHARE = 0.7      # fAerodynamicsDistribution for patched cars (EXPERIMENTAL:
                                 # assumed higher = more on the front axle. If high-speed
                                 # understeer gets WORSE, try 0.3)

# --- drifting (vehicletweaks.lib DriftHelper): stop it from cutting grip on
# plain steering input, so slides come mainly from the handbrake.
DRIFT_UNDER_TARGET_GRIP = 1.0    # vanilla 0.6 - 1.0 (grip multiplier while "drift" builds)
DRIFT_STEER_TIME = 3.0           # seconds of held steering before helper engages (vanilla 0.1 - 1)
HANDBRAKE_YAW_HELPER = True      # handbrake helps rotate the car into a drift
# LaunchHelper: after a standstill it cuts rear traction to 50-90% for ~1.5 s and
# (on many cars) sways the rear side to side - low-speed tail-happiness.
DISABLE_LAUNCH_HELPER = True
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, os.pardir, "mod", "responsive_steering",
                                    "workspace", "generated", "databases", "generic"))
FILES = ("vehiclesteering", "vehicletweaks", "vehicleaxle", "vehiclemilliken", "vehicleaerodynamics")

FLOAT = r'(<field name="{}" type="Float">)([^<]*)(</field>)'


def get(obj, name):
    m = re.search(FLOAT.format(name), obj)
    return float(m.group(2)) if m else None


def put(obj, name, value):
    return re.sub(FLOAT.format(name), lambda m: m.group(1) + f"{value:g}" + m.group(3), obj, count=1)


BOOL = r'(<field name="{}" type="Boolean">)([^<]*)(</field>)'


def put_bool(obj, name, value):
    return re.sub(BOOL.format(name), lambda m: m.group(1) + str(bool(value)) + m.group(3), obj)


def libobjs(xml):
    # split top-level <object name="libobj"> blocks (indent 2)
    return re.split(r'(?m)(?=^  <object name="libobj">)', xml)


def is_heavy(name):
    return ".Heavy" in name


def patch_steering(xml, log):
    parts = libobjs(xml)
    for i, o in enumerate(parts):
        m = re.search(r'name="text_hidName" type="String">([^<]*)<', o)
        if not m:
            continue
        name = m.group(1)
        if ".Bike" in name or "toy" in name.lower():
            continue                                  # already instant / special
        max_angle = get(o, "fMaxAngle")
        turn, ctr = get(o, "fTurnSteerSpeed"), get(o, "fCounterSteerSpeed")
        t = HEAVY_FULL_LOCK_TIME if is_heavy(name) else CAR_FULL_LOCK_TIME
        new_turn = round(max_angle / t)          # same feel on every car
        new_ctr = round(new_turn * COUNTER_STEER_MULT)
        o = put(o, "fTurnSteerSpeed", new_turn)
        o = put(o, "fCounterSteerSpeed", new_ctr)
        cap = get(o, "fCounterSteerSpeedCap")
        if cap is not None:
            o = put(o, "fCounterSteerSpeedCap", max(cap, new_ctr))
        if FORCE_MAX_ANGLE:
            o = put_bool(o, "bAlwaysForceMaxAngle", True)
        if SPEED_SENSITIVE_STEER:
            block = re.search(r'( *)<object name="MotorcycleMaxSteerAngle">.*?</object>\n', o, re.S)
            ind = block.group(1) + "  "
            new = (f'{block.group(1)}<object name="MotorcycleMaxSteerAngle">\n'
                   f'{ind}<field name="bUseSpeedForMaxAngle" type="Boolean">True</field>\n'
                   f'{ind}<field name="fSpeedForMinSteerAngle" type="Float">{SPEED_LOW:g}</field>\n'
                   f'{ind}<field name="fSpeedForMaxSteerAngle" type="Float">{SPEED_HIGH:g}</field>\n'
                   f'{ind}<field name="fSteerAngleAtMinSpeed" type="Float">{round(max_angle * LOW_SPEED_ANGLE_SCALE, 2):g}</field>\n'
                   f'{ind}<field name="fSteerAngleAtMaxSpeed" type="Float">{min(ANGLE_AT_HIGH, max_angle):g}</field>\n'
                   f'{block.group(1)}</object>\n')
            o = o[:block.start()] + new + o[block.end():]
        parts[i] = o
        log.append(f"{name:48} turn {turn:6.1f} -> {new_turn:4}  counter {ctr:6.1f} -> {new_ctr:4}"
                   f"  ({max_angle / turn:.2f}s -> {max_angle / new_turn:.2f}s to full lock)")
    return "".join(parts)


def patch_tweaks(xml, log):
    parts = libobjs(xml)
    for i, o in enumerate(parts):
        m = re.search(r'name="text_hidName" type="String">([^<]*)<', o)
        if not m or ".Bike" in m.group(1) or "toy" in m.group(1).lower():
            continue
        target = HEAVY_YAW_INERTIA if is_heavy(m.group(1)) else CAR_YAW_INERTIA
        old = get(o, "fChassisYawInertiaScale")
        notes = []
        if target is not None and old is not None and old > target:   # only ever reduce
            o = put(o, "fChassisYawInertiaScale", target)
            notes.append(f"yaw inertia {old:g}->{target:g}")
        u = get(o, "fLateralFrictionScaleUnderTarget")
        if u is not None and u < DRIFT_UNDER_TARGET_GRIP:
            o = put(o, "fLateralFrictionScaleUnderTarget", DRIFT_UNDER_TARGET_GRIP)
            notes.append(f"drift grip cut {u:g}->{DRIFT_UNDER_TARGET_GRIP:g}")
        t = get(o, "fSteeringTimeBeforeHelperActivation")
        if t is not None and t < DRIFT_STEER_TIME:
            o = put(o, "fSteeringTimeBeforeHelperActivation", DRIFT_STEER_TIME)
            notes.append(f"drift steer-time {t:g}->{DRIFT_STEER_TIME:g}")
        if HANDBRAKE_YAW_HELPER:
            o = put_bool(o, "bUseHandBrakeYawHelper", True)
        if DISABLE_LAUNCH_HELPER:
            o = put_bool(o, "bUseLaunchHelper", False)
            o = put_bool(o, "bUseBurnoutOscillation", False)
        parts[i] = o
        if notes:
            log.append(f"{m.group(1):48} " + ", ".join(notes))
    return "".join(parts)


def patch_axle(xml, log):
    parts = libobjs(xml)
    for i, o in enumerate(parts):
        m = re.search(r'name="text_hidName" type="String">([^<]*)<', o)
        if not m or ".Bike" in m.group(1) or "toy" in m.group(1).lower():
            continue
        lat = REAR_LATERAL_GRIP if m.group(1).startswith("Axle.BaseRear") else FRONT_LATERAL_GRIP
        old = get(o, "fLateralFriction")
        o = put(o, "fLateralFriction", round(old * lat, 3))
        o = put(o, "fLongitudinalFriction", round(get(o, "fLongitudinalFriction") * LONGITUDINAL_GRIP, 3))
        parts[i] = o
        log.append(f"{m.group(1):48} lateral grip x{lat:g}, longitudinal x{LONGITUDINAL_GRIP:g}")
    return "".join(parts)


def patch_milliken(xml, log):
    parts = libobjs(xml)
    for i, o in enumerate(parts):
        m = re.search(r'name="text_hidName" type="String">([^<]*)<', o)
        if not m or ".Bike" in m.group(1) or "toy" in m.group(1).lower():
            continue
        old = get(o, "fLateralStiffness")
        if old is not None:
            o = put(o, "fLateralStiffness", round(old * LATERAL_STIFFNESS_MULT, 3))
        if old is not None and m.group(1).split(" (")[0].endswith(".rear"):
            o = put(o, "fLateralStiffness", round(old * LATERAL_STIFFNESS_MULT * REAR_STIFFNESS_EXTRA, 3))
        ld = get(o, "fFrictionLoadDependencyFactor")
        if ld is not None and ld > MAX_FRICTION_LOAD_DEPENDENCY:
            o = put(o, "fFrictionLoadDependencyFactor", MAX_FRICTION_LOAD_DEPENDENCY)
        # handbrake must still be able to break the rear loose despite extra grip
        o = put_bool(o, "bBypassLongSlipCappingWithHandbrake", True)
        parts[i] = o
        log.append(f"{m.group(1):48} lateral stiffness {old:g} -> {old * LATERAL_STIFFNESS_MULT:g}")
    return "".join(parts)


def patch_aero(xml, log):
    parts = libobjs(xml)
    for i, o in enumerate(parts):
        m = re.search(r'name="text_hidName" type="String">([^<]*)<', o)
        if not m or DOWNFORCE_CDA is None:
            continue
        name = m.group(1)
        if ".Bike" in name or "toy" in name.lower() or is_heavy(name):
            continue
        old = get(o, "fLiftCdaFront")
        if old is None or old <= DOWNFORCE_CDA:
            continue
        o = put(o, "fLiftCdaFront", DOWNFORCE_CDA)
        o = put(o, "fAerodynamicsDistribution", DOWNFORCE_FRONT_SHARE)
        parts[i] = o
        log.append(f"{name:48} lift {old:g} -> {DOWNFORCE_CDA:g}")
    return "".join(parts)


def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"failed: {args}\n{r.stdout}\n{r.stderr}")


def main():
    ap = argparse.ArgumentParser(description="Build Responsive Steering .lib files")
    ap.add_argument("game", help="Watch_Dogs install folder")
    ap.add_argument("--gibbed", help="folder with Gibbed.Disrupt.*.exe")
    a = ap.parse_args()
    tools = a.gibbed or os.path.join(a.game, "hV_WD_ModdingKit_PLUS", "Tools", "Gibbed Tools")
    unpack = os.path.join(tools, "Gibbed.Disrupt.Unpack.exe")
    convert = os.path.join(tools, "Gibbed.Disrupt.ConvertBinaryObject.exe")
    for f in (unpack, convert, os.path.join(a.game, "data_win64", "patch.fat")):
        if not os.path.isfile(f):
            sys.exit(f"not found: {f}")
    tmp = tempfile.mkdtemp(prefix="wd_steer_")
    try:
        run(unpack, "-o", "--nu", "-f=databases\\\\generic\\\\vehicle(" + "|".join(n[7:] for n in FILES) + ")\\.lib",
            os.path.join(a.game, "data_win64", "patch.fat"), tmp)
        src = os.path.join(tmp, "generated", "databases", "generic")
        os.makedirs(OUT, exist_ok=True)
        log = []
        for name, fn in zip(FILES, (patch_steering, patch_tweaks, patch_axle,
                                    patch_milliken, patch_aero)):
            lib = os.path.join(src, name + ".lib")
            xml_path = os.path.join(tmp, name + ".xml")
            run(convert, "-e", "--nme", lib, xml_path)
            with open(xml_path, encoding="utf-8-sig") as f:
                xml = f.read()
            xml = fn(xml, log)
            with open(xml_path, "w", encoding="utf-8-sig") as f:
                f.write(xml)
            run(convert, "-i", xml_path, os.path.join(OUT, name + ".lib"))
        report = os.path.join(HERE, "changes.txt")
        with open(report, "w") as f:
            f.write("\n".join(log) + "\n")
        print("\n".join(log))
        # refresh the hand-editable XML copies used by compile.bat
        xdir = os.path.join(HERE, "xml")
        os.makedirs(xdir, exist_ok=True)
        for n in FILES:
            shutil.copy(os.path.join(tmp, n + ".xml"), os.path.join(xdir, n + ".xml"))
        print(f"\nwrote {OUT}\nrefreshed {xdir}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
