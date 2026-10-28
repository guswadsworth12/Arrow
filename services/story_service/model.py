"""Source bundle and structured operation contract; no persistence or I/O."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def reject(message: str) -> None:
    raise ServiceError(422, "invalid_source", message)


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            reject("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def _depth(value: Any) -> None:
    pending = [(value, 0)]
    while pending:
        item, level = pending.pop()
        if level > 64:
            reject("JSON nesting exceeds 64 levels")
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, float) and not math.isfinite(item):
            reject("JSON numbers must be finite")
        elif isinstance(item, dict):
            for key in item:
                if not isinstance(key, str):
                    reject("JSON object keys must be strings")
                key.encode("utf-8")
            pending.extend((value, level + 1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, level + 1) for value in item)


def parse_json(text: str | bytes) -> Any:
    try:
        value = json.loads(text, object_pairs_hook=_pairs,
                           parse_constant=lambda value: reject(f"Non-finite JSON number: {value}"))
        _depth(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        reject(f"Invalid JSON: {error}")


def canonical(value: Any) -> str:
    try:
        _depth(value)
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        text.encode("utf-8")
        return text
    except (ValueError, TypeError, UnicodeError, RecursionError):
        reject("Source must contain finite, valid UTF-8 JSON values")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_text(value: Any) -> str:
    canonical(value)  # Reject NaN, lone surrogates and non-JSON objects.
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def identifier(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value) is not None


def source_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        reject("Source paths must be relative strings of at most 512 characters")
    parts = value.split("/")
    if any(part in ("", ".", "..") or len(part.encode("utf-8")) > 200 for part in parts):
        reject("Source paths cannot contain empty, dot or parent segments")
    if any(char in value for char in "\\:") or any(ord(char) < 32 for char in value):
        reject("Source paths cannot contain backslashes, colons or control characters")
    if PurePosixPath(value).suffix.lower() not in (".arrow", ".json"):
        reject("Sources must use .arrow or .json extensions")
    return value


def bundle_shape(bundle: Any) -> str:
    """Validate paths before writing anything into a compiler staging directory."""
    if not isinstance(bundle, dict) or set(bundle) != {"manifest_path", "package", "documents"}:
        reject("Bundle requires exactly manifest_path, package and documents")
    encoded = canonical(bundle).encode("utf-8")
    if len(encoded) > 8 * 1024 * 1024:
        reject("A complete source bundle cannot exceed 8 MiB")
    manifest = source_path(bundle["manifest_path"])
    if "/" in manifest or not manifest.endswith(".json"):
        reject("manifest_path must be a .json filename, without directories")
    package, documents = bundle["package"], bundle["documents"]
    if not isinstance(package, dict) or not identifier(package.get("id")):
        reject("Package id must be an ASCII identifier of at most 64 characters")
    specs = package.get("documents")
    if not isinstance(specs, dict) or not 1 <= len(specs) <= 128 or not isinstance(documents, dict):
        reject("A package must contain 1 to 128 documents")
    if set(documents) != set(specs):
        reject("Bundle document keys must match the package's document inventory")
    paths = {manifest.casefold(), ".arrow-story-checkout.json"}
    if manifest.casefold() == ".arrow-story-checkout.json":
        reject("The checkout metadata filename is reserved")
    for chapter, spec in specs.items():
        if not identifier(chapter) or not isinstance(spec, dict) or not isinstance(documents[chapter], dict):
            reject("Each chapter needs an identifier, a manifest specification and an Arrow object")
        resources = documents[chapter].get("resources")
        if not isinstance(resources, dict) or any(not isinstance(resources.get(key), dict) for key in ("nodes", "scenes", "characters", "variables")):
            reject("Chapter documents need complete Arrow resource collections")
        path = source_path(spec.get("path"))
        if path.casefold() in paths:
            reject("Chapter paths must be distinct from each other and the manifest, including case")
        paths.add(path.casefold())
    # A source file cannot also serve as an ancestor directory of another source.
    for path in paths:
        if any(str(parent) in paths for parent in PurePosixPath(path).parents if str(parent) != "."):
            reject("Source file paths cannot overlap directory paths")
    canonical(bundle)
    return package["id"]


def _object(value: Any, allowed: set, required: set) -> dict:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        reject(f"Operation requires {sorted(required)}; allowed fields are {sorted(allowed)}")
    return value


def _document(bundle: dict, name: Any) -> dict:
    if not isinstance(name, str) or name not in bundle["documents"]:
        reject("Unknown chapter")
    return bundle["documents"][name]


def _node(bundle: dict, operation: dict) -> tuple[dict, str]:
    document = _document(bundle, operation.get("document"))
    node_id = operation.get("node")
    if not isinstance(node_id, str) or node_id not in document["resources"]["nodes"]:
        reject("Unknown node UID; use canonical decimal strings")
    return document, node_id


def _apply_operations(original: dict, operations: Any) -> dict:
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        reject("Supply between 1 and 100 structured operations")
    bundle = copy.deepcopy(original)
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
            reject("Each operation needs an op string")
        kind = operation["op"]
        if kind == "replace_bundle":
            _object(operation, {"op", "bundle"}, {"op", "bundle"})
            if len(operations) != 1:
                reject("replace_bundle must be the only operation")
            bundle_shape(operation["bundle"])
            bundle = copy.deepcopy(operation["bundle"])
        elif kind == "replace_document":
            _object(operation, {"op", "document", "value"}, {"op", "document", "value"})
            _document(bundle, operation["document"])
            if not isinstance(operation["value"], dict):
                reject("A replacement document must be an Arrow object")
            bundle["documents"][operation["document"]] = copy.deepcopy(operation["value"])
        elif kind == "update_node":
            _object(operation, {"op", "document", "node", "data", "name"}, {"op", "document", "node"})
            document, node_id = _node(bundle, operation)
            node = document["resources"]["nodes"][node_id]
            if "data" in operation:
                if not isinstance(operation["data"], dict):
                    reject("update_node data must be an object of fields to merge")
                node["data"].update(copy.deepcopy(operation["data"]))
            if "name" in operation:
                if not isinstance(operation["name"], str):
                    reject("Node name must be text")
                node["name"] = operation["name"]
        elif kind in ("set_connection", "disconnect"):
            fields = {"op", "document", "node", "slot"}
            if kind == "set_connection":
                fields.add("target")
            _object(operation, fields, fields)
            document, node_id = _node(bundle, operation)
            slot = operation["slot"]
            if type(slot) is not int or slot < 0:
                reject("Output slot must be a nonnegative integer")
            scene = next((scene for scene in document["resources"]["scenes"].values() if node_id in scene["map"]), None)
            if scene is None:
                reject("Node must have a scene placement")
            placement = scene["map"][node_id]
            wires = placement.get("io", [])
            remaining = [wire for wire in wires if wire[1] != slot]
            if kind == "disconnect" and len(remaining) == len(wires):
                reject("There is no connection at that output slot")
            if kind == "set_connection":
                target = operation["target"]
                if not isinstance(target, str) or target not in scene["map"]:
                    reject("Connections must target a node in the same scene; use explicit jumps otherwise")
                remaining.append([int(node_id), slot, int(target), 0])
            placement["io"] = remaining
        elif kind in ("set_link", "remove_link"):
            fields = {"op", "document", "node"}
            if kind == "set_link":
                fields |= {"target_document", "target_node"}
            _object(operation, fields, fields)
            _node(bundle, operation)
            links = bundle["package"].get("links", [])
            source = {"document": operation["document"], "node": operation["node"]}
            remaining = [link for link in links if link["from"] != source]
            if kind == "remove_link" and len(remaining) == len(links):
                reject("No external link exists at that node")
            if kind == "set_link":
                _node(bundle, {"document": operation["target_document"], "node": operation["target_node"]})
                remaining.append({"from": source, "to": {"document": operation["target_document"], "node": operation["target_node"]}})
            bundle["package"]["links"] = remaining
        elif kind in ("set_variable_default", "set_character_name"):
            collection = "variables" if kind == "set_variable_default" else "characters"
            field = "init" if collection == "variables" else "name"
            _object(operation, {"op", "key", "value"}, {"op", "key", "value"})
            key = operation["key"]
            if not isinstance(key, str) or key not in bundle["package"]["registry"].get(collection, {}):
                reject("Unknown shared registry key")
            bundle["package"]["registry"][collection][key][field] = copy.deepcopy(operation["value"])
            for chapter, spec in bundle["package"]["documents"].items():
                for local_id, shared_key in spec.get(collection, {}).items():
                    if shared_key == key:
                        bundle["documents"][chapter]["resources"][collection][local_id][field] = copy.deepcopy(operation["value"])
        else:
            reject(f"Unsupported operation: {kind}")
        # A replacement may be structurally malformed. Reject before subsequent
        # operations can dereference it; native Godot performs semantic validation.
        bundle_shape(bundle)
        for document in bundle["documents"].values():
            resources = document.get("resources")
            if not isinstance(resources, dict) or any(not isinstance(resources.get(key), dict) for key in ("nodes", "scenes", "characters", "variables")):
                reject("Replacement documents need complete resource collections")
            for node in resources["nodes"].values():
                if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
                    reject("Replacement nodes need data objects")
    if bundle["package"]["id"] != original["package"]["id"]:
        reject("A revision cannot rename the story id")
    return bundle


def changes(before: Any, after: Any, path: str = "") -> list[dict]:
    if type(before) is type(after) and isinstance(before, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in before and key in after:
                result.extend(changes(before[key], after[key], child))
            else:
                result.append({"path": child, "before_exists": key in before, "after_exists": key in after,
                               "before": before.get(key), "after": after.get(key)})
        return result
    if type(before) is not type(after) or before != after:
        return [{"path": path, "before_exists": True, "after_exists": True, "before": before, "after": after}]
    return []


def apply_operations(original: dict, operations: Any) -> dict:
    try:
        return _apply_operations(original, operations)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, RecursionError) as error:
        raise ServiceError(422, "invalid_operation", "An operation references malformed source data") from error
