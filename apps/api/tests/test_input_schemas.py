"""Every request body and query model refuses fields it does not know.

There is no shared base class for input schemas, so `extra="forbid"` is repeated in each of them
and a new schema can easily miss it. Unknown fields must fail loudly: a client sending `price` or
`user_id` should learn at once that the server ignores it rather than believe it was taken.
"""

import inspect
import typing

from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel

from conftest import SettingsFactory
from flagman.core.app import create_app


def _types(annotation: object) -> list[object]:
    """The annotation and everything nested in it: list[X], X | None, Annotated[X, ...]."""
    found = [annotation]
    for argument in typing.get_args(annotation):
        found += _types(argument)
    return found


def _models(annotation: object, seen: set[type[BaseModel]]) -> None:
    for candidate in _types(annotation):
        if not (inspect.isclass(candidate) and issubclass(candidate, BaseModel)):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        for field in candidate.model_fields.values():
            _models(field.annotation, seen)


def _input_models(dependant: Dependant, seen: set[type[BaseModel]]) -> None:
    # Headers and cookies are left out on purpose: a header model with extra="forbid" would turn
    # away every request that carries an ordinary header the model does not list.
    for param in [*dependant.body_params, *dependant.query_params]:
        _models(param.field_info.annotation, seen)
    for sub_dependant in dependant.dependencies:
        _input_models(sub_dependant, seen)


def test_every_input_model_forbids_unknown_fields(make_settings: SettingsFactory) -> None:
    models: set[type[BaseModel]] = set()
    for context in iter_route_contexts(create_app(make_settings()).routes):
        dependant = getattr(context.original_route, "dependant", None)
        if dependant is not None:
            _input_models(dependant, models)

    names = {model.__name__ for model in models}
    # The walk itself must keep working: bodies, query models and nested models are all found.
    assert {"ReserveIn", "HistoryQuery", "LocationIn"} <= names, sorted(names)
    lenient = sorted(
        f"{model.__module__}.{model.__name__}"
        for model in models
        if model.model_config.get("extra") != "forbid"
    )
    assert lenient == []
