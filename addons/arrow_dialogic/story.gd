@tool
extends RefCounted
## File-based story package linker. Arrow documents remain authored sources.
const Compiler = preload("compiler.gd")
const FORMAT = "arrow-story"
var errors: Array[String] = []
var identities: Dictionary = {}

func compile(path: String, package: Dictionary, source_text: String, character_bindings: Dictionary = {}) -> Dictionary:
	errors.clear()
	identities.clear()
	if not path.begins_with("res://"):
		return _failure("Story source must be inside the Godot project (res://)")
	if not package.get("format") is String or package.format != FORMAT or not _integer(package.get("version")) or package.version != 1 or not _name(package.get("id")) or not package.get("title") is String:
		return _failure("Story requires format=arrow-story, version=1, an identifier id, and a title")
	for key in ["documents", "registry", "entry"]:
		if not package.get(key) is Dictionary:
			return _failure("Story %s must be an object" % key)
	if package.documents.is_empty() or not package.get("links", []) is Array:
		return _failure("Story needs documents and a links array")
	for collection in ["characters", "variables"]:
		if not package.registry.get(collection, {}) is Dictionary:
			return _failure("Registry %s must be an object" % collection)
	var combined = {"title": package.title, "entry": -1, "resources": {"nodes": {}, "scenes": {}, "characters": {}, "variables": {}}}
	var resources: Dictionary = combined.resources
	var registry_ids = {"characters": {}, "variables": {}}
	var variable_names: Array[String] = []
	for collection in registry_ids:
		for key in _keys(package.registry.get(collection, {})):
			var item = package.registry[collection][key]
			if not _name(key) or not item is Dictionary:
				return _failure("Invalid registry %s key or object: %s" % [collection, key])
			if collection == "characters" and not item.get("name") is String:
				return _failure("Character %s needs a display name string" % key)
			if collection == "variables":
				if not _name(item.get("name")) or not Compiler.new()._literal_matches(item.get("init"), item.get("type")):
					return _failure("Variable %s needs a valid name, type and initial value" % key)
				if item.name in variable_names:
					return _failure("Duplicate registry variable name: " + item.name)
				variable_names.append(item.name)
			var id = _id(collection, key)
			registry_ids[collection][key] = id
			resources[collection][id] = item.duplicate(true)
	var bindings: Dictionary = {}
	for key in character_bindings:
		if not registry_ids.characters.has(key) or not character_bindings[key] is String:
			return _failure("Unknown or malformed story character binding %s" % key)
	for key in registry_ids.characters:
		bindings[registry_ids.characters[key]] = character_bindings.get(key, "arrow_character_%s_%s" % [path.sha256_text().left(8), registry_ids.characters[key]])
	var documents: Dictionary = {}
	var local_ids: Dictionary = {}
	var labels: Dictionary = {}
	var document_labels: Dictionary = {}
	var chapter_entries: Dictionary = {}
	var document_dependencies: Dictionary = {}
	var dependencies: Array = [{"path": path, "sha256": source_text.sha256_text()}]
	var paths: Array[String] = []
	for chapter in _keys(package.documents):
		var spec = package.documents[chapter]
		if not _name(chapter) or not spec is Dictionary or not spec.get("path") is String:
			return _failure("Invalid document entry %s" % chapter)
		var chapter_path = _resolve(path, spec.path)
		if chapter_path.is_empty() or chapter_path.to_lower() in paths or not FileAccess.file_exists(chapter_path):
			return _failure("Chapter %s needs a unique, existing path beneath the story manifest directory" % chapter)
		paths.append(chapter_path.to_lower())
		var text = FileAccess.get_file_as_string(chapter_path)
		var parsed = JSON.new()
		if parsed.parse(text) != OK or not parsed.data is Dictionary:
			return _failure("Chapter %s is not valid Arrow JSON: %s" % [chapter, parsed.get_error_message()])
		var document: Dictionary = parsed.data
		# Check every field before any ID remapping or typed conversion. External
		# jump placeholders temporarily point to themselves for local validation.
		var checker = Compiler.new()
		var checked = document.duplicate(true)
		if not checker._validate_shape(checked):
			return _failure("Chapter %s: %s" % [chapter, "; ".join(checker.errors)])
		for link in package.get("links", []):
			if not _reference(link.get("from") if link is Dictionary else null, package.documents) or not _reference(link.get("to") if link is Dictionary else null, package.documents):
				return _failure("Malformed story link; use document and canonical node ID strings")
			if link.from.document == chapter:
				var node = checked.resources.get("nodes", {}).get(link.from.node)
				if not node is Dictionary or node.get("type") != "jump" or node.data.get("target") != -1:
					return _failure("Link source %s/%s must be a jump with target -1" % [chapter, link.from.node])
				node.data.target = int(link.from.node)
		var local_bindings: Dictionary = {}
		for id in checked.resources.get("characters", {}):
			local_bindings[id] = "arrow_local_" + str(id)
		var local = checker.compile(checked, local_bindings)
		if not local.ok:
			return _failure("Chapter %s: %s" % [chapter, "; ".join(local.errors)])
		var mapping = {"nodes": {}, "scenes": {}, "characters": {}, "variables": {}}
		for collection in ["nodes", "scenes"]:
			for id in document.resources.get(collection, {}):
				mapping[collection][id] = _id(collection, chapter + "/" + id)
		for collection in ["characters", "variables"]:
			var declared = spec.get(collection, {})
			if not declared is Dictionary or declared.size() != document.resources.get(collection, {}).size():
				return _failure("Chapter %s must explicitly map every %s resource to the registry" % [chapter, collection])
			var used: Array = []
			for id in declared:
				var key = declared[id]
				if not document.resources.get(collection, {}).has(id) or not key is String or not registry_ids[collection].has(key) or key in used:
					return _failure("Chapter %s: unknown or duplicate %s mapping %s" % [chapter, collection, id])
				used.append(key)
				var canonical = package.registry[collection][key]
				var authored = document.resources[collection][id]
				for field in (["name", "type", "init"] if collection == "variables" else ["name"]):
					if authored.get(field) != canonical.get(field):
						return _failure("Chapter %s: %s %s differs from registry field %s" % [chapter, collection, id, field])
				mapping[collection][id] = registry_ids[collection][key]
		documents[chapter] = document
		local_ids[chapter] = mapping
		dependencies.append({"path": chapter_path, "sha256": text.sha256_text()})
		document_labels[chapter] = {}
		document_dependencies[chapter] = []
		for id in mapping.nodes:
			var label = "arrow_%s__%s" % [chapter, id]
			labels[mapping.nodes[id]] = label
			document_labels[chapter][id] = label
		chapter_entries[chapter] = document_labels[chapter][str(int(document.entry))]
	# All chapters have been validated. Only now rewrite references in private copies.
	for chapter in _keys(documents):
		var document: Dictionary = documents[chapter]
		var mapping: Dictionary = local_ids[chapter]
		for id in document.resources.nodes:
			var node = document.resources.nodes[id].duplicate(true)
			var data: Dictionary = node.data
			if data.has("character") and data.character >= 0:
				if not mapping.characters.has(str(int(data.character))):
					return _failure("Chapter %s node %s references an unknown character" % [chapter, id])
				data.character = int(mapping.characters[str(int(data.character))])
			if node.type in ["condition", "variable_update"]:
				data.variable = int(mapping.variables[str(int(data.variable))])
				if data.with[0] == 1:
					data.with[1] = int(mapping.variables[str(int(data.with[1]))])
			if node.type == "jump" and data.get("target", -1) >= 0:
				data.target = int(mapping.nodes[str(int(data.target))])
			resources.nodes[mapping.nodes[id]] = node
		for id in document.resources.scenes:
			var scene = document.resources.scenes[id].duplicate(true)
			var remapped: Dictionary = {}
			for node_id in scene.map:
				var placement = scene.map[node_id].duplicate(true)
				for wire in placement.get("io", []):
					wire[0] = int(mapping.nodes[str(int(wire[0]))])
					wire[2] = int(mapping.nodes[str(int(wire[2]))])
				remapped[mapping.nodes[node_id]] = placement
			scene.map = remapped
			if scene.get("entry", -1) >= 0:
				scene.entry = int(mapping.nodes[str(int(scene.entry))])
			resources.scenes[mapping.scenes[id]] = scene
	var linked: Array[String] = []
	for link in package.get("links", []):
		var from_id = local_ids[link.from.document].nodes[link.from.node]
		if from_id in linked or not local_ids[link.to.document].nodes.has(link.to.node):
			return _failure("Duplicate link source or missing target: %s/%s" % [link.from.document, link.from.node])
		linked.append(from_id)
		if link.from.document != link.to.document and not link.to.document in document_dependencies[link.from.document]:
			document_dependencies[link.from.document].append(link.to.document)
		resources.nodes[from_id].data.target = int(local_ids[link.to.document].nodes[link.to.node])
	if not _reference(package.entry, documents) or not local_ids[package.entry.document].nodes.has(package.entry.node):
		return _failure("Story entry references an unknown document or node")
	combined.entry = int(local_ids[package.entry.document].nodes[package.entry.node])
	if not errors.is_empty():
		return _failure("Story ID collision")
	for targets in document_dependencies.values():
		targets.sort()
	var result = Compiler.new().compile(combined, bindings, labels)
	if result.ok:
		result.manifest.merge({"format": 2, "story_id": package.id, "dependencies": dependencies,
			"document_labels": document_labels, "chapter_entries": chapter_entries, "document_dependencies": document_dependencies, "registry_ids": registry_ids, "links": package.get("links", []).duplicate(true),
			"character_bindings": bindings, "source": path, "source_sha256": source_text.sha256_text()}, true)
	return result

# Report stale dependencies without changing authored sources, generated files or state.
static func dependency_changes(manifest: Dictionary) -> Array[String]:
	var changed: Array[String] = []
	var dependencies = manifest.get("dependencies", [{"path": manifest.get("source", ""), "sha256": manifest.get("source_sha256", "")}])
	if not dependencies is Array or dependencies.is_empty():
		return ["Invalid dependency list"]
	for dependency in dependencies:
		if not dependency is Dictionary or not dependency.get("path") is String or not dependency.get("sha256") is String:
			changed.append("Invalid dependency record")
		elif not FileAccess.file_exists(dependency.path) or FileAccess.get_file_as_string(dependency.path).sha256_text() != dependency.sha256:
			changed.append(dependency.path)
	return changed

func _resolve(manifest: String, relative: String) -> String:
	if relative.is_empty() or relative.is_absolute_path() or ":" in relative or "\\" in relative:
		return ""
	for part in relative.split("/"):
		if part in ["", ".", ".."]:
			return ""
	var resolved = manifest.get_base_dir().path_join(relative)
	var access = DirAccess.open("res://")
	var current = "res://"
	for part in resolved.trim_prefix("res://").split("/"):
		current = current.path_join(part)
		if access.is_link(current):
			return ""
	return resolved

func _reference(value: Variant, documents: Dictionary) -> bool:
	return value is Dictionary and value.get("document") is String and documents.has(value.document) and value.get("node") is String and Compiler.new()._valid_id(value.node)

func _name(value: Variant) -> bool:
	return Compiler.new()._identifier(value)

func _integer(value: Variant) -> bool:
	return Compiler.new()._integer(value, 0)

func _keys(value: Dictionary) -> Array:
	var keys = value.keys()
	keys.sort_custom(func(a, b): return str(a) < str(b))
	return keys

func _id(kind: String, key: String) -> String:
	var identity = kind + ":" + key
	var id = str(("0x" + identity.sha256_text().left(12)).hex_to_int())
	if identities.has(id) and identities[id] != identity:
		errors.append("Stable resource ID collision: " + identity)
	identities[id] = identity
	return id

func _failure(message: String) -> Dictionary:
	errors.append(message)
	return {"ok": false, "errors": errors.duplicate(), "timeline": ""}
