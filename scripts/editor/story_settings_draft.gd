extends RefCounted
## One immutable source revision and a bounded batch of structured edits.
const Compiler = preload("res://addons/arrow_dialogic/compiler.gd")
var source: Dictionary = {}
var groups: Dictionary = {}
var error = ""

func load_source(value: Dictionary) -> void:
	source = value.duplicate(true)
	groups.clear()
	error = ""

func operations() -> Array:
	var result: Array = []
	for group in groups.values():
		result.append_array(group.operations.duplicate(true))
	return result

func stage_character(key: String, value: String) -> bool:
	var registry: Dictionary = source.bundle.package.registry.get("characters", {})
	if not registry.has(key):
		return _fail("Choose a shared character first.")
	return _stage("character:" + key, "Character %s → %s" % [key, value],
		[] if value == registry[key].name else [{"op": "set_character_name", "key": key, "value": value}])

func stage_variable(key: String, value: Variant) -> bool:
	var registry: Dictionary = source.bundle.package.registry.get("variables", {})
	if not registry.has(key) or not Compiler.new()._literal_matches(value, registry[key].type):
		return _fail("Use a value matching the variable type. Numbers must be whole numbers within ±9007199254740991.")
	return _stage("variable:" + key, "Default %s → %s" % [key, JSON.stringify(value)],
		[] if value == registry[key].init else [{"op": "set_variable_default", "key": key, "value": value}])

func stage_link(chapter: String, jump: String, target_chapter: String, target: String) -> bool:
	if not _jump(chapter, jump) or not _node(target_chapter, target):
		return _fail("Choose an existing Jump and destination node.")
	var original = link(chapter, jump)
	var edits: Array = []
	if original.is_empty() or original.to.document != target_chapter or original.to.node != target:
		edits = [{"op": "update_node", "document": chapter, "node": jump, "data": {"target": -1}},
			{"op": "set_link", "document": chapter, "node": jump, "target_document": target_chapter, "target_node": target}]
	return _stage("jump:" + chapter + "/" + jump, "%s/%s → %s/%s" % [chapter, jump, target_chapter, target], edits)

func stage_local_jump(chapter: String, jump: String, target: String) -> bool:
	if not _jump(chapter, jump) or not _node(chapter, target):
		return _fail("Choose an existing Jump and a destination in the same chapter.")
	var edits: Array = []
	if not link(chapter, jump).is_empty():
		edits.append({"op": "remove_link", "document": chapter, "node": jump})
	if not edits.is_empty() or source.bundle.documents[chapter].resources.nodes[jump].data.target != int(target):
		edits.append({"op": "update_node", "document": chapter, "node": jump, "data": {"target": int(target)}})
	return _stage("jump:" + chapter + "/" + jump, "%s/%s → local node %s" % [chapter, jump, target], edits)

func link(chapter: String, jump: String) -> Dictionary:
	for item in source.bundle.package.get("links", []):
		if item.from.document == chapter and item.from.node == jump:
			return item
	return {}

func _node(chapter: String, id: String) -> bool:
	return source.bundle.documents.has(chapter) and source.bundle.documents[chapter].resources.nodes.has(id)

func _jump(chapter: String, id: String) -> bool:
	return _node(chapter, id) and source.bundle.documents[chapter].resources.nodes[id].type == "jump"

func _stage(key: String, label: String, edits: Array) -> bool:
	error = ""
	var count = operations().size() - groups.get(key, {"operations": []}).operations.size() + edits.size()
	if count > 100:
		return _fail("A proposal can contain at most 100 operations. Propose this batch before staging more changes.")
	if edits.is_empty():
		groups.erase(key)
	else:
		groups[key] = {"label": label, "operations": edits.duplicate(true)}
	return true

func _fail(message: String) -> bool:
	error = message
	return false
