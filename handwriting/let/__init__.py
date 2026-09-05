"""Compatibility setup for the legacy LET application."""

# scipy-weave still imports NumPy's removed Tester helper during package
# initialisation.  LET never uses that test runner, but providing this tiny
# placeholder keeps the maintained standalone weave package usable with the
# pinned NumPy runtime.
import numpy.testing


if not hasattr(numpy.testing, 'Tester'):
  class _LegacyTester:
    def __init__(self, *args, **kwargs):
      pass

    def test(self, *args, **kwargs):
      raise RuntimeError('The legacy NumPy test runner is not available')

  numpy.testing.Tester = _LegacyTester
