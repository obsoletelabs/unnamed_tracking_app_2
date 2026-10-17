"""Filter declarative integrations at the public host permission boundary."""

import math
from collections.abc import Mapping

from .contracts import Capability, PluginUiDocument, UiFieldType
from .ui_placement import place_navigation, place_settings

# Controls whose emptied optional value the declarative renderer saves as "".
_CLEARABLE_FIELD_TYPES = frozenset({UiFieldType.NUMBER, UiFieldType.SELECT})


def filter_ui_document(
    document: PluginUiDocument,
    effective_capabilities: frozenset[str],
) -> PluginUiDocument:
    """Remove host integrations that this installation is not authorized to mount."""

    def permitted(capability: Capability) -> bool:
        return capability.value in effective_capabilities

    navigation_capabilities = {
        "main.sidebar": Capability.FRONTEND_NAVIGATION_MAIN,
        "settings.sidebar": Capability.FRONTEND_NAVIGATION_SETTINGS,
        "administration": Capability.FRONTEND_NAVIGATION_ADMIN,
        "game.context": Capability.FRONTEND_CONTEXT_GAME,
        "media.context": Capability.FRONTEND_CONTEXT_MEDIA,
    }
    context_capabilities = {
        "game": Capability.FRONTEND_CONTEXT_GAME,
        "media": Capability.FRONTEND_CONTEXT_MEDIA,
        "documents": Capability.FRONTEND_CONTEXT_DOCUMENTS,
    }
    extension_capabilities = {
        "app.global": Capability.FRONTEND_OVERLAY,
        "home.replace": Capability.FRONTEND_PAGE_REPLACE_HOME,
        "administration.users.actions": Capability.FRONTEND_NAVIGATION_ADMIN,
        "administration.users.settings": Capability.FRONTEND_SETTINGS,
        "administration.password-policy.after": Capability.FRONTEND_SETTINGS,
    }
    authorized_routes = document.routes if permitted(Capability.FRONTEND_ROUTES) else ()
    authorized_settings = (
        tuple(place_settings(item, effective_capabilities) for item in document.settings_sections)
        if permitted(Capability.FRONTEND_SETTINGS)
        else ()
    )
    authorized_route_ids = {item.id for item in authorized_routes}
    authorized_settings_ids = {item.id for item in authorized_settings}
    return document.model_copy(
        update={
            "native_frontend": (
                document.native_frontend if permitted(Capability.FRONTEND_NATIVE) else None
            ),
            "navigation": tuple(
                place_navigation(item, effective_capabilities)
                for item in document.navigation
                if permitted(navigation_capabilities[item.location.value])
                and (item.route_id is None or item.route_id in authorized_route_ids)
                and (
                    item.settings_section_id is None
                    or item.settings_section_id in authorized_settings_ids
                )
            ),
            "settings_sections": authorized_settings,
            "connections": (
                document.connections if permitted(Capability.FRONTEND_CONNECTIONS) else ()
            ),
            "extensions": tuple(
                item
                for item in document.extensions
                if permitted(
                    extension_capabilities.get(item.slot.value, Capability.FRONTEND_PAGE_EXTEND)
                )
            ),
            "home_widgets": (
                document.home_widgets if permitted(Capability.FRONTEND_HOME_WIDGETS) else ()
            ),
            "themes": document.themes if permitted(Capability.FRONTEND_THEMES) else (),
            "shortcuts": tuple(
                item
                for item in document.shortcuts
                if permitted(Capability.FRONTEND_SHORTCUTS)
                and (item.route_id is None or item.route_id in authorized_route_ids)
                and (item.when_route_id is None or item.when_route_id in authorized_route_ids)
                and (
                    item.action_id is None
                    or all(
                        action.capability is None
                        or action.capability.name.value in effective_capabilities
                        for action in document.actions
                        if action.id == item.action_id
                    )
                )
            ),
            "overlays": (document.overlays if permitted(Capability.FRONTEND_OVERLAY) else ()),
            "dialog_contributions": (
                document.dialog_contributions if permitted(Capability.FRONTEND_DIALOG) else ()
            ),
            "contextual_actions": tuple(
                item
                for item in document.contextual_actions
                if permitted(context_capabilities[item.location.value])
            ),
            "document_readers": (
                document.document_readers
                if permitted(Capability.FRONTEND_CONTEXT_DOCUMENTS)
                and permitted(Capability.DOCUMENTS_READ)
                else ()
            ),
            "routes": authorized_routes,
            "page_replacements": tuple(
                item
                for item in document.page_replacements
                if permitted(Capability(f"frontend.page.replace.{item.page.value}"))
            ),
        }
    )


def filter_setting_values(
    document: PluginUiDocument,
    saved: Mapping[str, object],
) -> dict[str, object]:
    """Release saved values only for declared, non-secret fields that still fit them.

    Secret and password fields are write-only, so their stored values never reach the
    browser. Undeclared keys stay runtime-private. A value that no longer matches an
    updated declaration's type or options is omitted so the field shows its default.
    """
    fields = [field for section in document.settings for field in section.fields]
    concealed = {field.id for field in fields if field.secret or field.type is UiFieldType.PASSWORD}
    values: dict[str, object] = {}
    for field in fields:
        if field.id in concealed or field.id not in saved:
            continue
        value = saved[field.id]
        if value == "" and not field.required and field.type in _CLEARABLE_FIELD_TYPES:
            values[field.id] = value
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        candidate = (
            tuple(value)
            if field.type is UiFieldType.MULTISELECT and isinstance(value, list)
            else value
        )
        try:
            field.validate_value(candidate)
        except ValueError:
            continue
        values[field.id] = value
    return values
