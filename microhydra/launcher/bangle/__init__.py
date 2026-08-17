#bangle7.1hydra
import sys
path = "/launcher/bangle"
if path not in sys.path:
    sys.path.insert(0, path)
import launcher.bangle.main
