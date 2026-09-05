"""Small persistent cache for parsed PLY2 training files."""

import hashlib
import os
import pickle
import tempfile

from ply2 import ply2


CACHE_VERSION = 1
CACHE_ROOT = os.environ.get(
  'MTYH_CACHE_DIR',
  os.path.join(os.path.expanduser('~'), '.cache', 'my-text-your-handwriting', 'ply2-v1'))

_hits = 0
_misses = 0


def _signature(filename):
  stat = os.stat(filename)
  return (CACHE_VERSION, os.path.abspath(filename), stat.st_size, stat.st_mtime_ns)


def _cache_filename(filename):
  key = hashlib.sha256(os.path.abspath(filename).encode('utf-8')).hexdigest()
  return os.path.join(CACHE_ROOT, key + '.pickle')


def read(filename):
  """Reads a PLY2 file, reusing a validated local parse cache when possible."""
  global _hits, _misses

  signature = _signature(filename)
  cached = _cache_filename(filename)

  try:
    with open(cached, 'rb') as handle:
      record = pickle.load(handle)
    if record.get('signature') == signature:
      _hits += 1
      return record['data']
  except (FileNotFoundError, EOFError, OSError, pickle.PickleError, AttributeError, ValueError):
    pass

  _misses += 1
  data = ply2.read(filename)

  os.makedirs(CACHE_ROOT, exist_ok=True)
  fd, temporary = tempfile.mkstemp(prefix='.ply2-', suffix='.tmp', dir=CACHE_ROOT)
  try:
    with os.fdopen(fd, 'wb') as handle:
      pickle.dump({'signature': signature, 'data': data}, handle, pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, cached)
  except Exception:
    try:
      os.unlink(temporary)
    except OSError:
      pass
    # A cache write must never prevent loading the training data.

  return data


def stats():
  return {'hits': _hits, 'misses': _misses, 'directory': CACHE_ROOT}
