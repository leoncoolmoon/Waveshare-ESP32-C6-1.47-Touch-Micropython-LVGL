import sys
path = "/apps/bangle7hydra"
if path not in sys.path:
    sys.path.insert(0, path)
import apps.bangle7hydra.main
