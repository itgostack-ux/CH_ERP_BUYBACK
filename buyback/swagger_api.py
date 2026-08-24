"""OpenAPI discovery for the supported Buyback mobile API."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import frappe
from frappe.utils import get_url


_API_MODULES = (
    "buyback.api",
    "buyback.public_portal_api",
    "buyback.payment_api",
    "buyback.lifecycle_api",
    "buyback.exchange_lifecycle",
    "buyback.buyback.dashboard_api",
    "buyback.buyback.sla_engine",
    "buyback.buyback.scorecards",
    "buyback.buyback.page.buyback_hub.buyback_hub_api",
)


def _schema(parameter: inspect.Parameter) -> dict[str, Any]:
    annotation = parameter.annotation
    annotation_name = str(annotation)
    if annotation is int or "int" in annotation_name:
        return {"type": "integer"}
    if annotation is float or "float" in annotation_name:
        return {"type": "number"}
    if annotation is bool or "bool" in annotation_name:
        return {"type": "boolean"}
    if annotation is list or "list" in annotation_name:
        return {"type": "array", "items": {"type": "object"}}
    return {"type": "string"}


def _operation(function, method: str) -> dict[str, Any]:
    parameters = [
        parameter
        for parameter in inspect.signature(function).parameters.values()
        if parameter.name not in {"self", "cls"}
        and parameter.kind
        not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    ]
    required = [parameter.name for parameter in parameters if parameter.default is inspect.Parameter.empty]
    operation = {
        "operationId": f"{function.__module__}.{function.__name__}",
        "summary": (inspect.getdoc(function) or function.__name__).splitlines()[0],
        "responses": {
            "200": {
                "description": "Successful Frappe response. The payload is in the `message` property.",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            "403": {"description": "Missing role, document permission, or scope."},
            "417": {"description": "Business validation failed."},
            "429": {"description": "Rate limited."},
        },
    }
    if function in frappe.guest_methods:
        operation["security"] = []
    else:
        operation["security"] = [{"ApiTokenAuth": []}]

    if method == "get":
        operation["parameters"] = [
            {
                "name": parameter.name,
                "in": "query",
                "required": parameter.name in required,
                "schema": _schema(parameter),
            }
            for parameter in parameters
        ]
    else:
        properties = {parameter.name: _schema(parameter) for parameter in parameters}
        operation["requestBody"] = {
            "required": bool(required),
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            },
        }
    return operation


def _recommended_method(function) -> str:
    allowed = frappe.allowed_http_methods_for_whitelisted_func.get(function, [])
    if len(allowed) == 1:
        return allowed[0].lower()
    return "get" if function.__name__.startswith(("get_", "search_", "check_", "lookup_")) else "post"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_buyback_openapi() -> dict[str, Any]:
    """Return the current OpenAPI 3.0 document for supported Buyback methods."""
    paths: dict[str, dict[str, Any]] = {}
    for module_name in _API_MODULES:
        module = importlib.import_module(module_name)
        for _, function in inspect.getmembers(module, inspect.isfunction):
            if function.__module__ != module_name or function not in frappe.whitelisted:
                continue
            route = f"/api/method/{module_name}.{function.__name__}"
            paths[route] = {_recommended_method(function): _operation(function, _recommended_method(function))}

    return {
        "openapi": "3.0.3",
        "info": {"title": "Buyback Mobile API", "version": "1.0.0"},
        "servers": [{"url": get_url()}],
        "paths": dict(sorted(paths.items())),
        "components": {
            "securitySchemes": {
                "ApiTokenAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": "Enter `token <api_key>:<api_secret>`.",
                }
            }
        },
    }
