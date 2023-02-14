import dataclasses as dc
from copy import deepcopy
from inspect import isclass
from typing import Any, Collection, List, Optional, Type

import pydantic as pd

import pydantic_xml as pxml
from pydantic_xml import errors
from pydantic_xml.element import XmlElementReader, XmlElementWriter
from pydantic_xml.serializers.encoder import XmlEncoder
from pydantic_xml.serializers.serializer import Location, PydanticShapeType, Serializer
from pydantic_xml.utils import QName, merge_nsmaps


class HomogeneousSerializerFactory:
    """
    Homogeneous collection type serializer factory.
    """

    class TextSerializer(Serializer):
        def __init__(
                self, model: Type['pxml.BaseXmlModel'], model_field: pd.fields.ModelField, ctx: Serializer.Context,
        ):
            assert model_field.sub_fields and len(model_field.sub_fields) == 1
            if (
                isclass(model_field.type_) and issubclass(model_field.type_, pxml.BaseXmlModel) or
                issubclass(model_field.type_, tuple)
            ):
                raise errors.ModelFieldError(
                     model.__name__, model_field.name, "Inline list value should be of scalar type",
                )

        def serialize(
                self, element: XmlElementWriter, value: Collection[Any], *, encoder: XmlEncoder,
                skip_empty: bool = False,
        ) -> Optional[XmlElementWriter]:
            if value is None or skip_empty and len(value) == 0:
                return element

            encoded = " ".join(encoder.encode(val) for val in value)
            element.set_text(encoded)
            return element

        def deserialize(self, element: Optional[XmlElementReader]) -> Optional[List[Any]]:
            if element is None:
                return None

            text = element.pop_text()

            if text is None:
                return []

            return [value for value in text.split()]

    class AttributeSerializer(Serializer):
        def __init__(
                self, model: Type['pxml.BaseXmlModel'], model_field: pd.fields.ModelField, ctx: Serializer.Context,
        ):
            assert model_field.sub_fields and len(model_field.sub_fields) == 1
            if issubclass(model_field.type_, pxml.BaseXmlModel):
                raise errors.ModelFieldError(
                     model.__name__, model_field.name, "Inline list value should be of scalar type",
                )

            _, ns, nsmap = self._get_entity_info(model_field)

            name = model_field.name

            assert name is not None, "attr must be name"

            self.attr_name = QName.from_alias(tag=name, ns=ns, nsmap=nsmap, is_attr=True).uri

        def serialize(
                self, element: XmlElementWriter, value: Collection[Any], *, encoder: XmlEncoder,
                skip_empty: bool = False,
        ) -> Optional[XmlElementWriter]:
            if value is None or skip_empty and len(value) == 0:
                return element

            encoded = " ".join(encoder.encode(val) for val in value)
            element.set_attribute(self.attr_name, encoded)
            return element

        def deserialize(self, element: Optional[XmlElementReader]) -> Optional[List[Any]]:
            if element is None:
                return None

            attribute = element.pop_attrib(self.attr_name)

            if attribute is None:
                return []

            return [value for value in attribute.split()]

    class ElementSerializer(Serializer):
        def __init__(
                self, model: Type['pxml.BaseXmlModel'], model_field: pd.fields.ModelField, ctx: Serializer.Context,
        ):
            assert model_field.sub_fields is not None, "unexpected model field"

            name, ns, nsmap = self._get_entity_info(model_field)
            name = name or model_field.alias
            ns = ns or ctx.parent_ns
            nsmap = merge_nsmaps(nsmap, ctx.parent_nsmap)

            self._element_name = QName.from_alias(tag=name, ns=ns, nsmap=nsmap).uri

            item_field = deepcopy(model_field.sub_fields[0])
            item_field.name = model_field.name
            item_field.alias = model_field.alias
            item_field.field_info = model_field.field_info

            self._inner_serializer = self._build_field_serializer(
                model,
                item_field,
                dc.replace(
                    ctx,
                    parent_is_root=False,
                    parent_ns=ns,
                    parent_nsmap=nsmap,
                ),
            )

        def serialize(
                self, element: XmlElementWriter, value: List[Any], *, encoder: XmlEncoder, skip_empty: bool = False,
        ) -> Optional[XmlElementWriter]:
            if value is None:
                return element

            if skip_empty and len(value) == 0:
                return element

            for val in value:
                if skip_empty and val is None:
                    continue

                self._inner_serializer.serialize(element, val, encoder=encoder, skip_empty=skip_empty)

            return element

        def deserialize(self, element: Optional[XmlElementReader]) -> Optional[List[Any]]:
            if element is None:
                return None

            result = []
            while (value := self._inner_serializer.deserialize(element)) is not None:
                result.append(value)

            return result or None

    @classmethod
    def build(
            cls,
            model: Type['pxml.BaseXmlModel'],
            model_field: pd.fields.ModelField,
            field_location: Location,
            ctx: Serializer.Context,
    ) -> 'Serializer':
        assert model_field.sub_fields is not None, "unexpected model field"
        assert len(model_field.sub_fields) == 1, "unexpected subfields number"

        is_root = model.__custom_root_type__
        item_field = model_field.sub_fields[0]

        if PydanticShapeType.from_shape(item_field.shape) in (
            PydanticShapeType.HOMOGENEOUS,
            PydanticShapeType.HETEROGENEOUS,
        ):
            raise errors.ModelFieldError(
                model.__name__, model_field.name, "collection elements can't be of collection type",
            )

        if is_root and field_location is Location.MISSING:
            raise errors.ModelFieldError(
                model.__name__, model_field.name, "root model collections should be marked as elements",
            )

        if field_location is Location.ELEMENT:
            return cls.ElementSerializer(model, model_field, ctx)
        elif field_location is Location.MISSING:
            return cls.TextSerializer(model, model_field, ctx)
        elif field_location is Location.ATTRIBUTE:
            return cls.AttributeSerializer(model, model_field, ctx)
        else:
            raise AssertionError("unreachable")
