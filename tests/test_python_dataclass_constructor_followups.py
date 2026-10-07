"""Pinned Pyrefly/Ty constructor regressions, with native control models."""
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def worker():
    path = Path(os.environ.get('BLT_INSPECT_WORKER', Path(__file__).resolve().parents[1] / 'src/basilisp_tools/_inspect.py'))
    spec = importlib.util.spec_from_file_location('_fourth_dataclass_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_source(worker, tmp_path, source):
    (tmp_path / 'fixture.py').write_text(source)
    return worker.StaticInspector([str(tmp_path)], []).module('fixture')['members']


def test_undecorated_subclass_preserves_inherited_dataclass_fields(worker, tmp_path):
    source = '''from dataclasses import dataclass
@dataclass
class A:
    w: int
class B(A):
    x: str
@dataclass
class C(B):
    y: bytes
@dataclass
class D(C):
    z: float
'''
    result = inspect_source(worker, tmp_path, source)
    assert [p['name'] for p in result['B']['parameters']] == ['w']
    assert [p['name'] for p in result['C']['parameters']] == ['w', 'y']
    assert [p['name'] for p in result['D']['parameters']] == ['w', 'y', 'z']
    native = {}
    exec(source, native)
    assert native['C'](w=0, y=b'1').w == 0
    assert native['D'](0, b'1', 2.0).z == 2.0
    with pytest.raises(TypeError):
        native['C'](y=b'1')
    with pytest.raises(TypeError):
        native['B'](w=0, x='not a dataclass field')


def test_transform_factory_keyword_supplies_default(worker, tmp_path):
    source = '''from typing import Any
from typing_extensions import dataclass_transform
from dataclasses import dataclass, field as native_field
def field(*, factory) -> Any:
    return native_field(default_factory=factory)
@dataclass_transform(field_specifiers=(field,))
def build(cls):
    return dataclass(cls)
@build
class C:
    x: int = field(factory=int)
'''
    result = inspect_source(worker, tmp_path, source)
    assert result['C']['parameters'][0]['required?'] is False
    native = {}
    exec(source, native)
    assert native['C']().x == 0
    assert native['C'](x=3).x == 3
    with pytest.raises(TypeError):
        native['C'](other=3)


@pytest.mark.parametrize('specifiers', ['', 'field_specifiers=(other_field,)'])
def test_transform_honors_only_its_declared_field_specifiers(worker, tmp_path, specifiers):
    source = f'''from typing import Any
from typing_extensions import dataclass_transform
from dataclasses import field, dataclass
def other_field(**kwargs) -> Any: ...
@dataclass_transform({specifiers})
def model(cls):
    def init(self, name=cls.name):
        self.name = name
    cls.__init__ = init
    return cls
@model
class A:
    name: str = field(init=False)
@dataclass
class B:
    name: str = field(init=False)
'''
    result = inspect_source(worker, tmp_path, source)
    assert [(p['name'], p['required?']) for p in result['A']['parameters']] == [('name', False)]
    assert result['B']['parameters'] == []
    native = {}
    exec(source, native)
    assert native['A'](name='foo').name == 'foo'
    with pytest.raises(TypeError):
        native['B'](name='foo')


def test_transform_explicit_stdlib_field_specifier_still_honors_init_false(worker, tmp_path):
    result = inspect_source(worker, tmp_path, '''from typing_extensions import dataclass_transform
from dataclasses import field
@dataclass_transform(field_specifiers=(field,))
class Model: ...
class A(Model):
    name: str = field(init=False)
''')
    assert result['A']['parameters'] == []
