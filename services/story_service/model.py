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


# Arrow's native distributed UID (scripts/shared/flake_id_helpers.gd, bit sizes
# [10, 6, 37] in settings.gd): chapter, author and per-author incremental seed.
SERVICE_AUTHOR = "Arrow Story Service"
CHAPTER_LIMIT, AUTHOR_LIMIT, SEED_LIMIT = 2 ** 10, 2 ** 6, 2 ** 37
CHAPTER_SHIFT, AUTHOR_SHIFT = 43, 37
MAX_EXACT_INTEGER = 2 ** 53 - 1  # The native compiler rejects larger JSON IDs.
REFERENCE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]{0,63})")


def base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    text = ""
    while True:
        value, digit = divmod(value, 36)
        text = digits[digit] + text
        if value == 0:
            return text


def _allocate(document: dict) -> str:
    """Allocate a resource UID exactly as Arrow does, under a dedicated service author.

    The service never advances a human contributor's seed, so chapters later edited
    in Arrow cannot collide with service-created resources.
    """
    meta = document.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("authors"), dict) or type(meta.get("chapter")) is not int:
        reject("Creating resources needs Arrow chapter and author metadata; save the chapter in Arrow first")
    if type(meta.get("epoch")) is int and meta["epoch"] > 0:
        reject("This chapter uses time-based Snowflake UIDs; create its resources in Arrow")
    chapter, authors = meta["chapter"], meta["authors"]
    if not 0 <= chapter < CHAPTER_LIMIT:
        reject("The chapter id is outside Arrow's native UID range")
    author = next((key for key, value in authors.items()
                   if isinstance(value, list) and len(value) == 2 and value[0] == SERVICE_AUTHOR), None)
    if author is None:
        author = next((str(key) for key in range(1, AUTHOR_LIMIT) if str(key) not in authors), None)
        if author is None:
            reject("No free Arrow author slot remains for service-created resources")
        authors[author] = [SERVICE_AUTHOR, 0]
    if not author.isdigit() or not 0 < int(author) < AUTHOR_LIMIT:
        reject("The service author has an invalid Arrow author id")
    used = {uid for collection in document["resources"].values() if isinstance(collection, dict) for uid in collection}
    seed = authors[author][1]
    while True:
        if type(seed) is not int or not 0 <= seed < SEED_LIMIT:
            reject("The service author's Arrow seed is exhausted or malformed")
        uid = (chapter << CHAPTER_SHIFT) | (int(author) << AUTHOR_SHIFT) | seed
        seed += 1
        if uid > MAX_EXACT_INTEGER:
            reject("The allocated UID would exceed the compiler's exact integer range")
        if str(uid) not in used:
            authors[author][1] = seed
            return str(uid)


def _reference(value: Any, references: dict, document: Any) -> Any:
    """Resolve an "@ref" alias to a UID created earlier in this batch."""
    match = REFERENCE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return value
    if match[1] not in references:
        reject(f"Unknown reference {value}; create it earlier in the same batch")
    owner, uid = references[match[1]]
    if owner != document:
        reject(f"Reference {value} belongs to chapter {owner}")
    return uid


def _data_references(value: Any, references: dict, document: str) -> Any:
    """Replace exact "@ref" strings for resources created in this batch with integer UIDs."""
    if isinstance(value, dict):
        return {key: _data_references(item, references, document) for key, item in value.items()}
    if isinstance(value, list):
        return [_data_references(item, references, document) for item in value]
    match = REFERENCE.fullmatch(value) if isinstance(value, str) else None
    if match is not None and match[1] in references:
        return int(_reference(value, references, document))
    return value


def _resolve(operation: dict, references: dict) -> dict:
    operation = dict(operation)
    document = operation.get("document")
    for field in ("node", "target"):
        if field in operation:
            operation[field] = _reference(operation[field], references, document)
    if "target_node" in operation:
        operation["target_node"] = _reference(operation["target_node"], references, operation.get("target_document"))
    if isinstance(operation.get("data"), dict):
        operation["data"] = _data_references(operation["data"], references, document)
    return operation


def _register(references: dict, operation: dict, document: str, uid: str) -> None:
    if "ref" not in operation:
        return
    if not identifier(operation["ref"]) or operation["ref"] in references:
        reject("Each ref must be a new identifier within the batch")
    references[operation["ref"]] = (document, uid)


def _add_shared(bundle: dict, document: str, collection: str, operation: dict, fields: tuple) -> str:
    """Create a chapter-local character or variable mapped to the package registry."""
    chapter = _document(bundle, document)
    local = {field: copy.deepcopy(operation[field]) for field in fields if field in operation}
    key = operation.get("key", local.get("name"))
    if not identifier(key):
        reject("Registry keys must be identifiers; pass key or an identifier-style name")
    registry = bundle["package"].setdefault("registry", {}).setdefault(collection, {})
    mapping = bundle["package"]["documents"][document].setdefault(collection, {})
    if key in mapping.values():
        reject(f"Chapter {document} already maps registry {collection} key {key}")
    if key in registry:
        for field, value in local.items():
            if registry[key].get(field) != value:
                reject(f"Registry {collection} {key} already has a different {field}")
        local = {field: copy.deepcopy(registry[key][field]) for field in fields if field in registry[key]}
    else:
        missing = [field for field in fields if field not in local]
        if missing:
            reject(f"A new registry entry needs {missing}")
        registry[key] = copy.deepcopy(local)
    uid = _allocate(chapter)
    if collection == "characters":
        local.update(color=operation.get("color", "ffffff"), tags={})
    chapter["resources"][collection][uid] = local
    mapping[uid] = key
    return uid


def _apply_operations(original: dict, operations: Any) -> dict:
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        reject("Supply between 1 and 100 structured operations")
    bundle = copy.deepcopy(original)
    references = {}
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
            reject("Each operation needs an op string")
        operation = _resolve(operation, references)
        kind = operation["op"]
        if kind == "add_node":
            _object(operation, {"op", "document", "type", "scene", "name", "data", "offset", "ref"}, {"op", "document", "type"})
            document = _document(bundle, operation["document"])
            if not identifier(operation["type"]):
                reject("Node type must be an Arrow node type name")
            data = operation.get("data", {})
            if not isinstance(data, dict):
                reject("add_node data must be an object")
            scenes = document["resources"]["scenes"]
            scene_id = operation.get("scene")
            if scene_id is None:
                if len(scenes) != 1:
                    reject("This chapter has several scenes; name the scene for the new node")
                scene_id = next(iter(scenes))
            if not isinstance(scene_id, str) or scene_id not in scenes:
                reject("Unknown scene UID; use canonical decimal strings")
            placements = scenes[scene_id]["map"]
            offset = operation.get("offset")
            if offset is None:
                # Arrow lays graphs out left to right; start a new column past the rightmost node.
                right = max((place["offset"][0] for place in placements.values()
                             if isinstance(place.get("offset"), list) and place["offset"]), default=-300)
                offset = [right + 300, 180]
            if not isinstance(offset, list) or len(offset) != 2 or any(type(value) is not int for value in offset):
                reject("Node offset must be two whole numbers")
            uid = _allocate(document)
            name = operation.get("name", base36(int(uid)))
            if not isinstance(name, str):
                reject("Node name must be text")
            document["resources"]["nodes"][uid] = {"type": operation["type"], "name": name, "data": copy.deepcopy(data)}
            placements[uid] = {"offset": offset, "io": []}
            _register(references, operation, operation["document"], uid)
        elif kind == "remove_node":
            _object(operation, {"op", "document", "node"}, {"op", "document", "node"})
            document, node_id = _node(bundle, operation)
            here = {"document": operation["document"], "node": node_id}
            if (int(node_id) == document.get("entry") or bundle["package"].get("entry") == here
                    or any(scene.get("entry") == int(node_id) for scene in document["resources"]["scenes"].values())):
                reject("A story, chapter or scene entry cannot be removed")
            del document["resources"]["nodes"][node_id]
            for scene in document["resources"]["scenes"].values():
                scene["map"].pop(node_id, None)
                for placement in scene["map"].values():
                    if "io" in placement:
                        placement["io"] = [wire for wire in placement["io"] if str(wire[2]) != node_id]
            if "links" in bundle["package"]:
                bundle["package"]["links"] = [link for link in bundle["package"]["links"] if here not in (link["from"], link["to"])]
        elif kind == "add_variable":
            _object(operation, {"op", "document", "name", "type", "init", "key", "ref"}, {"op", "document"})
            uid = _add_shared(bundle, operation["document"], "variables", operation, ("name", "type", "init"))
            _register(references, operation, operation["document"], uid)
        elif kind == "add_character":
            _object(operation, {"op", "document", "name", "color", "key", "ref"}, {"op", "document"})
            if "color" in operation and (not isinstance(operation["color"], str) or not re.fullmatch(r"[0-9a-fA-F]{6}", operation["color"])):
                reject("Character color must be six hexadecimal digits")
            uid = _add_shared(bundle, operation["document"], "characters", operation, ("name",))
            _register(references, operation, operation["document"], uid)
        elif kind == "replace_bundle":
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
