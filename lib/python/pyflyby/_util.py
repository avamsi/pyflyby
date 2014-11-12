# pyflyby/_util.py.
# Copyright (C) 2011, 2012, 2013, 2014 Karl Chen.
# License: MIT http://opensource.org/licenses/MIT

from __future__ import absolute_import, division, with_statement

from   contextlib               import contextmanager
import os
import sys
import types

def memoize(function):
    cache = {}
    def wrapped_fn(*args, **kwargs):
        cache_key = (args, tuple(sorted(kwargs.items())))
        try:
            return cache[cache_key]
        except KeyError:
            result = function(*args, **kwargs)
            cache[cache_key] = result
            return result
    wrapped_fn.cache = cache
    return wrapped_fn


class WrappedAttributeError(Exception):
    pass


class cached_attribute(object):
    '''Computes attribute value and caches it in instance.

    Example:
        class MyClass(object):
            @cached_attribute
            def myMethod(self):
                # ...
    Use "del inst.myMethod" to clear cache.'''
    # http://code.activestate.com/recipes/276643/

    def __init__(self, method, name=None):
        self.method = method
        self.name = name or method.__name__

    def __get__(self, inst, cls):
        if inst is None:
            return self
        try:
            result = self.method(inst)
        except AttributeError as e:
            raise WrappedAttributeError(str(e)), None, sys.exc_info()[2]
        setattr(inst, self.name, result)
        return result


def stable_unique(items):
    """
    Return a copy of C{items} without duplicates.  The order of other items is
    unchanged.

      >>> stable_unique([1,4,6,4,6,5,7])
      [1, 4, 6, 5, 7]
    """
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def longest_common_prefix(items1, items2):
    """
    Return the longest common prefix.

      >>> longest_common_prefix("abcde", "abcxy")
      'abc'

    @rtype:
      C{type(items1)}
    """
    n = 0
    for x1, x2 in zip(items1, items2):
        if x1 != x2:
            break
        n += 1
    return items1[:n]


def prefixes(parts):
    """
      >>> list(prefixes("abcd"))
      ['a', 'ab', 'abc', 'abcd']

    """
    for i in range(1, len(parts)+1):
        yield parts[:i]


def partition(iterable, predicate):
    """
      >>> partition('12321233221', lambda c: int(c) % 2 == 0)
      (['2', '2', '2', '2', '2'], ['1', '3', '1', '3', '3', '1'])

    """
    falses = []
    trues = []
    for item in iterable:
        if predicate(item):
            trues.append(item)
        else:
            falses.append(item)
    return trues, falses


Inf = float('Inf')


@contextmanager
def NullCtx():
    """
    Context manager that does nothing.
    """
    yield


@contextmanager
def ImportPathCtx(path_additions):
    """
    Context manager that temporarily prepends C{sys.path} with C{path_additions}.
    """
    if not isinstance(path_additions, (tuple, list)):
        path_additions = [path_additions]
    old_path = sys.path[:]
    sys.path[0:0] = path_additions
    try:
        yield
    finally:
        sys.path[:] = old_path


@contextmanager
def CwdCtx(path):
    """
    Context manager that temporarily enters a new working directory.
    """
    old_cwd = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(old_cwd)


@contextmanager
def EnvVarCtx(**kwargs):
    """
    Context manager that temporarily modifies os.environ.
    """
    unset = object()
    old = {}
    try:
        for k, v in kwargs.items():
            old[k] = os.environ.get(k, unset)
            os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is unset:
                del os.environ[k]
            else:
                os.environ[k] = v


@contextmanager
def ExcludeImplicitCwdFromPathCtx():
    """
    Context manager that temporarily removes "." from C{sys.path}.
    """
    old_path = sys.path
    try:
        sys.path = [p for p in sys.path if p not in (".", "")]
        yield
    finally:
        sys.path[:] = old_path



class _AdvisedFunction(object):
    """
    A wrapper around a function that calls some "around advice" hook.  The
    hook has access to a global named "__original__" as a reference to the
    original unadvised function.

    This is written as a class with a __call__ method.  We do so rather than
    using a metafunction, so that we can also implement __getattr_ to look
    through to the target.
    """

    def __init__(self, aspect, original, hook, name, doc):
        self.__aspect__   = aspect
        self.__original__ = original
        self.__hook       = hook
        self.__name__     = name
        self.__doc__      = doc

    def __call__(self, *args, **kwargs):
        hook = self.__hook
        # Temporarily add __original__ to the globals that the hook
        # function sees.
        globals = hook.__globals__
        UNSET = object()
        old_original = globals.get("__original__", UNSET)
        try:
            globals["__original__"] = self.__original__
            return hook(*args, **kwargs)
        finally:
            if old_original is UNSET:
                del globals["__original__"]
            else:
                globals["__original__"] = old_original

    def __getattr__(self, k):
        return getattr(self.__original__, k)



class Aspect(object):
    """
    Monkey-patch a target method (joinpoint) with "around" advice.

    The advice can call "__original__(...)".  At run time, a global named
    "__original__" will magically be available to the wrapped function.

    Suppose someone else wrote Foo.bar():
      >>> class Foo(object):
      ...     def __init__(self, x):
      ...         self.x = x
      ...     def bar(self, y):
      ...         return "bar(self.x=%s,y=%s)" % (self.x,y)

      >>> foo = Foo(42)

    To monkey patch C{foo.bar}, decorate the wrapper with C{"@advise(foo.bar)"}:
      >>> @advise(foo.bar)
      ... def addthousand(y):
      ...     return "advised foo.bar(y=%s): %s" % (y, __original__(y+1000))

      >>> foo.bar(100)
      'advised foo.bar(y=100): bar(self.x=42,y=1100)'

    You can uninstall the advice and get the original behavior back:
      >>> addthousand.unadvise()

      >>> foo.bar(100)
      'bar(self.x=42,y=100)'

    @see:
      U{http://en.wikipedia.org/wiki/Aspect-oriented_programming}
    """

    def __init__(self, joinpoint):
        if isinstance(joinpoint, types.MethodType):
            self._container = joinpoint.im_self or joinpoint.im_class
            self._name      = joinpoint.im_func.__name__
            self._original  = joinpoint
            assert joinpoint == getattr(self._container, self._name)
        elif isinstance(joinpoint, tuple) and len(joinpoint) == 2:
            container, name = joinpoint
            self._container = container
            self._name      = name
            self._original  = getattr(self._container, self._name)
        # TODO: FunctionType (for top-level functions)
        # TODO: unbound method
        # TODO: classmethod
        else:
            raise TypeError("JoinPoint: unexpected %s"
                            % (type(joinpoint).__name__,))
        self._wrapped = None

    def advise(self, hook):
        from pyflyby._log import logger
        logger.debug("advising %s.%s",
                     self._container.__class__.__name__, self._name)
        assert getattr(self._container, self._name) == self._original
        assert self._wrapped is None
        # Create the wrapped function.
        wrapped_name = "%s__advised__%s" % (self._name, hook.__name__)
        wrapped_doc = "%s.\n\nAdvice %s:\n%s" % (
            self._original.__doc__, hook.__name__, hook.__doc__)
        wrapped = _AdvisedFunction(self, self._original, hook,
                                   wrapped_name, wrapped_doc)
        self._wrapped = wrapped
        # Install the wrapped function!
        setattr(self._container, self._name, wrapped)
        return self

    def unadvise(self):
        if self._wrapped is None:
            return
        cur = getattr(self._container, self._name)
        if cur is self._wrapped:
            setattr(self._container, self._name, self._original)
            # TODO: should actually delattr for instancemethods
        elif cur == self._original:
            pass
        else:
            from pyflyby._log import logger
            logger.debug("%s seems modified; not unadvising it", self._name)
        self._wrapped = None


def advise(joinpoint):
    """
    Advise C{joinpoint}.

    See L{Aspect}.
    """
    aspect = Aspect(joinpoint)
    return aspect.advise