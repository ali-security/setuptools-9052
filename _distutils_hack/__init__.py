import sys
import os
import re
import importlib
import warnings
import contextlib


is_pypy = '__pypy__' in sys.builtin_module_names


warnings.filterwarnings('ignore',
                        r'.+ distutils\b.+ deprecated',
                        DeprecationWarning)


def warn_distutils_present():
    if 'distutils' not in sys.modules:
        return
    if is_pypy and sys.version_info < (3, 7):
        # PyPy for 3.6 unconditionally imports distutils, so bypass the warning
        # https://foss.heptapod.net/pypy/pypy/-/blob/be829135bc0d758997b3566062999ee8b23872b4/lib-python/3/site.py#L250
        return
    warnings.warn(
        "Distutils was imported before Setuptools, but importing Setuptools "
        "also replaces the `distutils` module in `sys.modules`. This may lead "
        "to undesirable behaviors or errors. To avoid these issues, avoid "
        "using distutils directly, ensure that setuptools is installed in the "
        "traditional way (e.g. not an editable install), and/or make sure "
        "that setuptools is always imported before distutils.")


def clear_distutils():
    if 'distutils' not in sys.modules:
        return
    warnings.warn("Setuptools is replacing distutils.")
    mods = [name for name in sys.modules if re.match(r'distutils\b', name)]
    for name in mods:
        del sys.modules[name]


def enabled():
    """
    Allow selection of distutils by environment variable.
    """
    which = os.environ.get('SETUPTOOLS_USE_DISTUTILS', 'local')
    return which == 'local'


def ensure_local_distutils():
    clear_distutils()

    # With the DistutilsMetaFinder in place,
    # perform an import to cause distutils to be
    # loaded from setuptools._distutils. Ref #2906.
    with shim():
        importlib.import_module('distutils')

    # check that submodules load as expected
    core = importlib.import_module('distutils.core')
    assert '_distutils' in core.__file__, core.__file__


def do_override():
    """
    Ensure that the local copy of distutils is preferred over stdlib.

    See https://github.com/pypa/setuptools/issues/417#issuecomment-392298401
    for more motivation.
    """
    if enabled():
        warn_distutils_present()
        ensure_local_distutils()


class suppress(contextlib.suppress, contextlib.ContextDecorator):
    """
    A version of contextlib.suppress with decorator support.

    >>> @suppress(KeyError)
    ... def key_error():
    ...     {}['']
    >>> key_error()
    """


class DistutilsMetaFinder:
    def find_spec(self, fullname, path, target=None):
        if path is not None:
            return

        method_name = 'spec_for_{fullname}'.format(**locals())
        method = getattr(self, method_name, lambda: None)
        return method()

    def spec_for_distutils(self):
        import importlib.abc
        import importlib.util

        try:
            mod = importlib.import_module('setuptools._distutils')
        except Exception:
            # There are a couple of cases where setuptools._distutils
            # may not be present:
            # - An older Setuptools without a local distutils is
            #   taking precedence. Ref #2957.
            # - Path manipulation during sitecustomize removes
            #   setuptools from the path but only after the hook
            #   has been loaded. Ref #2980.
            # In either case, fall back to stdlib behavior.
            return

        class DistutilsLoader(importlib.abc.Loader):

            def create_module(self, spec):
                return mod

            def exec_module(self, module):
                pass

        return importlib.util.spec_from_loader(
            'distutils', DistutilsLoader(), origin=mod.__file__
        )

    def spec_for_pip(self):
        """
        Ensure stdlib distutils when running under pip.
        See pypa/pip#8761 for rationale.
        """
        if self.pip_imported_during_build():
            return
        if self.is_get_pip():
            return
        clear_distutils()
        self.spec_for_distutils = lambda: None

    @classmethod
    def pip_imported_during_build(cls):
        """
        Detect if pip is being imported in a build script. Ref #2355.
        """
        import traceback
        return any(
            cls.frame_file_is_setup(frame)
            for frame, line in traceback.walk_stack(None)
        )

    @classmethod
    @suppress(AttributeError)
    def is_get_pip(cls):
        """
        Detect if get-pip is being invoked. Ref #2993.
        """
        import __main__
        return os.path.basename(__main__.__file__) == 'get-pip.py'

    @staticmethod
    def frame_file_is_setup(frame):
        """
        Return True if the indicated frame suggests a setup.py file.
        """
        # some frames may not have __file__ (#2940)
        return frame.f_globals.get('__file__', '').endswith('setup.py')


DISTUTILS_FINDER = DistutilsMetaFinder()


def add_shim():
    DISTUTILS_FINDER in sys.meta_path or insert_shim()


@contextlib.contextmanager
def shim():
    insert_shim()
    try:
        yield
    finally:
        remove_shim()


def insert_shim():
    sys.meta_path.insert(0, DISTUTILS_FINDER)


def remove_shim():
    try:
        sys.meta_path.remove(DISTUTILS_FINDER)
    except ValueError:
        pass
