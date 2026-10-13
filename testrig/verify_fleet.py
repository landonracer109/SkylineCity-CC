# Compares cityfleet jobs in a saved world with their build files (each file's "at" line places it).
#   python3 verify_fleet.py <world> <jobs dir> <job ids (comma list)>
import sys, os, subprocess
WORLD, JOBS, IDS = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
here = os.path.dirname(os.path.abspath(__file__))
for jid in IDS:
    f = os.path.join(JOBS, jid + ".txt")
    at = next(l.split()[1:4] for l in open(f) if l.startswith("at "))
    out = subprocess.run([sys.executable, os.path.join(here, "verify.py"), WORLD, f] + at, capture_output=True, text=True).stdout
    print(jid, out.strip().replace("\n", "\n    "))
