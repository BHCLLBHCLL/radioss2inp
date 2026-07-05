"""
Check ODB for stress/strain/displacement results.
Usage: abaqus python check_odb.py [odb_path]
"""
import sys
import os

try:
    from odbAccess import openOdb
except ImportError:
    print("ERROR: odbAccess not available. Run with: abaqus python check_odb.py")
    sys.exit(1)

odb_path = sys.argv[1] if len(sys.argv) > 1 else "Cell_Phone_Drop.odb"
if not os.path.exists(odb_path):
    print("ERROR: ODB file not found: {}".format(odb_path))
    sys.exit(1)

print("=" * 60)
print("Checking ODB: {}".format(odb_path))
print("File size: {:.1f} MB".format(os.path.getsize(odb_path) / 1e6))
print("=" * 60)

try:
    odb = openOdb(path=odb_path, readOnly=True)
except Exception as e:
    print("ERROR opening ODB: {}".format(e))
    sys.exit(1)

# Check steps
steps = odb.steps
print("\nNumber of steps: {}".format(len(steps)))

if len(steps) == 0:
    print("ERROR: No steps found in ODB!")
    odb.close()
    sys.exit(1)

for stepName in steps.keys():
    step = steps[stepName]
    print("\n--- Step: {} ---".format(stepName))
    print("  Total time: {:.6e}".format(step.totalTime))
    print("  Time period: {:.6e}".format(step.timePeriod))
    print("  Number of frames: {}".format(len(step.frames)))

    # Check frames
    for i, frame in enumerate(step.frames):
        print("\n  Frame {}: time={:.6e}".format(i, frame.frameValue))

        # Check field outputs
        fieldOutputs = frame.fieldOutputs
        print("  Number of field outputs: {}".format(len(fieldOutputs)))

        for fieldName in sorted(fieldOutputs.keys()):
            field = fieldOutputs[fieldName]
            try:
                values = field.values
                if len(values) > 0:
                    val = values[0].data
                    if hasattr(val, '__len__'):
                        val_str = str(val[:6]) if len(val) >= 6 else str(val)
                    else:
                        val_str = str(val)
                    print("    {}: {} values, sample={}".format(
                        fieldName, len(values), val_str))
                else:
                    print("    {}: 0 values".format(fieldName))
            except Exception as e:
                print("    {}: error reading values: {}".format(fieldName, e))

# Check for specific results
print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)

required_fields = {
    'S': 'Stress',
    'LE': 'Logarithmic Strain',
    'PEEQ': 'Equivalent Plastic Strain',
    'U': 'Displacement',
    'V': 'Velocity',
    'A': 'Acceleration'
}

found_fields = {}
for stepName in steps.keys():
    step = steps[stepName]
    if len(step.frames) > 0:
        # Check last frame for results
        lastFrame = step.frames[-1]
        fieldOutputs = lastFrame.fieldOutputs
        for fieldKey, fieldDesc in required_fields.items():
            if fieldKey in fieldOutputs:
                if fieldKey not in found_fields:
                    found_fields[fieldKey] = []
                found_fields[fieldKey].append(stepName)

print("\nFound results:")
for fieldKey, fieldDesc in required_fields.items():
    if fieldKey in found_fields:
        print("  [OK] {} ({}) - in steps: {}".format(
            fieldKey, fieldDesc, ', '.join(found_fields[fieldKey])))
    else:
        print("  [MISSING] {} ({})".format(fieldKey, fieldDesc))

if len(found_fields) >= 3:
    print("\n>>> SUCCESS: ODB contains result data!")
else:
    print("\n>>> WARNING: ODB may be missing result data!")

odb.close()
print("\nODB check complete.")
