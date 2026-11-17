from __future__ import annotations

from enum import StrEnum
from fractions import Fraction

from pydantic import Field, model_validator

from verityflow.ir.domains import (
    NumericDomain,
    continuous_domain,
    integer_domain,
    scaled_decimal_domain,
)
from verityflow.ir.model import Expr, NumericLit, referenced_fields
from verityflow.ir.units import get_unit
from verityflow.models.common import FrozenModel


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"


NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.DECIMAL})


class FieldDecl(FrozenModel):
    name: str = Field(min_length=1)
    type: FieldType
    nullable: bool = True
    unit: str | None = None
    min: NumericLit | None = None
    max: NumericLit | None = None
    scale: int | None = Field(default=None, ge=0)
    domain: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> FieldDecl:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"field {self.name!r}: min exceeds max")
        if self.unit is not None:
            if self.type not in NUMERIC_TYPES:
                raise ValueError(f"field {self.name!r}: units apply to numeric fields only")
            if get_unit(self.unit) is None:
                raise ValueError(f"field {self.name!r}: unknown unit {self.unit!r}")
        if self.type is FieldType.INTEGER and self.scale is not None:
            raise ValueError(f"field {self.name!r}: integer fields have no scale")
        if self.type is FieldType.DECIMAL and self.scale is None:
            # A decimal without a declared scale is a continuous rational domain.
            pass
        if self.type is FieldType.STRING and self.domain is None:
            pass
        if self.type is FieldType.BOOLEAN and self.domain is not None:
            raise ValueError(f"field {self.name!r}: boolean fields have no literal domain")
        if self.type is FieldType.STRING and self.domain is not None:
            if len(set(self.domain)) != len(self.domain):
                raise ValueError(f"field {self.name!r}: duplicate domain literals")
        return self

    @property
    def is_numeric(self) -> bool:
        return self.type in NUMERIC_TYPES


class DestinationDecl(FrozenModel):
    id: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()


class DataDictionary(FrozenModel):
    fields: tuple[FieldDecl, ...]
    destinations: tuple[DestinationDecl, ...] = ()
    aliases: dict[str, str] = Field(default_factory=dict)
    named_predicates: dict[str, Expr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> DataDictionary:
        names = [field.name.casefold() for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("duplicate field names")
        known = {field.name for field in self.fields}
        for alias, target in self.aliases.items():
            if target not in known:
                raise ValueError(f"alias {alias!r} targets unknown field {target!r}")
        for phrase, expression in self.named_predicates.items():
            unknown = referenced_fields(expression) - known
            if unknown:
                raise ValueError(
                    f"named predicate {phrase!r} references unknown fields {sorted(unknown)}"
                )
        destination_names = [destination.id.casefold() for destination in self.destinations]
        if len(set(destination_names)) != len(destination_names):
            raise ValueError("duplicate destination ids")
        all_destination_names = list(destination_names)
        for destination in self.destinations:
            all_destination_names.extend(alias.casefold() for alias in destination.aliases)
        if len(set(all_destination_names)) != len(all_destination_names):
            raise ValueError("destination ids and aliases must be unique")
        return self

    def field(self, name: str) -> FieldDecl | None:
        wanted = name.casefold()
        for field in self.fields:
            if field.name.casefold() == wanted:
                return field
        return None

    def resolve_field(self, token: str) -> str | None:
        direct = self.field(token)
        if direct is not None:
            return direct.name
        target = self.aliases.get(token.casefold())
        if target is None:
            return None
        return target

    def destination(self, token: str) -> str | None:
        wanted = token.strip().casefold()
        for destination in self.destinations:
            if destination.id.casefold() == wanted:
                return destination.id
            if any(alias.casefold() == wanted for alias in destination.aliases):
                return destination.id
        return None

    def destination_ids(self) -> tuple[str, ...]:
        return tuple(destination.id for destination in self.destinations)

    def numeric_domain(self, name: str) -> NumericDomain:
        field = self.field(name)
        if field is None:
            raise ValueError(f"unknown field {name!r}")
        minimum: Fraction | None = field.min
        maximum: Fraction | None = field.max
        if field.type is FieldType.INTEGER:
            return integer_domain(minimum, maximum)
        if field.type is FieldType.DECIMAL:
            if field.scale is not None:
                return scaled_decimal_domain(field.scale, minimum, maximum)
            return continuous_domain(minimum, maximum)
        raise ValueError(f"field {name!r} is not numeric")
