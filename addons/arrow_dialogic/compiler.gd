@tool
extends RefCounted
## Strict Arrow v3 JSON -> Dialogic 2 Alpha 20 timeline compiler.
## No Dialogic classes are referenced, so validation also works without Dialogic.

var errors: Array[String] = []
var nodes: Dictionary
var variables: Dictionary
var characters: Dictionary
var edges: Dictionary
var bindings: Dictionary
var labels: Dictionary
const COMPARISONS = {"eq": "==", "nq": "!=", "gt": ">", "gte": ">=", "ls": "<", "lse": "<="}
const MAX_EXACT_INTEGER = 9007199254740991
const SUPPORTED = ["entry", "content", "monolog", "dialog", "condition", "variable_update", "jump"]

func compile(document: Dictionary, character_bindings: Dictionary = {}, node_labels: Dictionary = {}) -> Dictionary:
	errors.clear()
	edges = {}
	bindings = character_bindings
	labels = node_labels
	if not _validate_shape(document):
		return {"ok": false, "errors": errors.duplicate(), "timeline": ""}
	var resources = document.get("resources", {})
	if not resources is Dictionary:
		return _failure("resources must be an object")
	for key in ["nodes", "scenes", "variables", "characters"]:
		if not resources.get(key, {}) is Dictionary:
			return _failure("resources.%s must be an object" % key)
	nodes = resources.get("nodes", {})
	variables = resources.get("variables", {})
	characters = resources.get("characters", {})
	if nodes.is_empty():
		return _failure("No nodes found")
	for id in nodes:
		if not id is String or not _valid_id(id) or not nodes[id] is Dictionary:
			return _failure("Invalid node ID or object: %s" % id)
		if not nodes[id].get("data", {}) is Dictionary:
			return _failure("Node %s data must be an object" % id)
		edges[str(id)] = {}
	var used_labels: Dictionary = {}
	for id in labels:
		if not nodes.has(id):
			return _failure("Label mapping references unknown node %s" % id)
	for id in nodes:
		var label = _label(id)
		if not _identifier(label) or used_labels.has(label):
			return _failure("Invalid or duplicate timeline label at node %s" % id)
		used_labels[label] = true
	for id in characters:
		if not id is String or not _valid_id(id) or not characters[id] is Dictionary or not characters[id].get("name") is String:
			return _failure("Invalid character %s" % id)
	for id in variables:
		var item = variables[id]
		if not id is String or not _valid_id(id) or not item is Dictionary or not _identifier(item.get("name", "")):
			return _failure("Variable %s needs an identifier-style name" % id)
		if not _literal_matches(item.get("init"), item.get("type", "")):
			return _failure("Variable %s has an unsupported type or default" % id)
	var names: Array = []
	for item in variables.values():
		if item.name in names:
			return _failure("Duplicate variable name: %s" % item.name)
		names.append(item.name)
	var seen_nodes: Dictionary = {}
	for scene_id in resources.get("scenes", {}):
		var scene = resources.scenes[scene_id]
		if not scene is Dictionary or not scene.get("map", {}) is Dictionary:
			return _failure("Invalid scene %s" % scene_id)
		if scene.has("entry") and (not _integer(scene.entry, -1) or (scene.entry >= 0 and (not scene.get("map", {}).has(str(int(scene.entry))) or nodes.get(str(int(scene.entry)), {}).get("type") != "entry"))):
			return _failure("Scene %s has an invalid entry" % scene_id)
		if scene.get("macro", false) != false:
			return _failure("Scene %s: macros are not supported" % scene_id)
		for raw_id in scene.get("map", {}):
			var id = str(raw_id)
			if not nodes.has(id) or seen_nodes.has(id):
				return _failure("Scene has missing or multiply owned node %s" % id)
			seen_nodes[id] = true
			var placement = scene.map[raw_id]
			if not placement is Dictionary or not placement.get("io", []) is Array:
				return _failure("Invalid graph placement at node %s" % id)
			if placement.get("skip", false):
				errors.append("Node %s: skipped nodes are not supported yet" % id)
			for wire in placement.get("io", []):
				if not wire is Array or wire.size() != 4 or not wire.all(func(value): return _integer(value, 0)):
					return _failure("Malformed connection at node %s" % id)
				var target = str(int(wire[2]))
				var slot = int(wire[1])
				if str(int(wire[0])) != id or not nodes.has(target) or slot < 0 or int(wire[3]) != 0:
					return _failure("Invalid connection at node %s" % id)
				if edges[id].has(slot):
					return _failure("Multiple targets for node %s slot %s" % [id, slot])
				edges[id][slot] = target
	for id in nodes:
		if not seen_nodes.has(id):
			errors.append("Node %s is not placed in a scene" % id)
	var entry = str(int(document.get("entry", -1)))
	if not nodes.has(entry) or nodes[entry].get("type") != "entry":
		return _failure("Missing project entry")
	var lines: Array[String] = ["# Generated from Arrow; edit the .arrow source.", "jump " + _label(entry)]
	var ids = nodes.keys()
	ids.sort_custom(func(a, b): return int(a) < int(b))
	for id in ids:
		lines.append("label " + _label(id))
		_emit_node(str(id), lines)
	# Every block ends in a jump or end_timeline: no accidental fallthrough.
	var defaults: Dictionary = {}
	for item in variables.values():
		defaults[item.name] = item.init
	if not errors.is_empty():
		return {"ok": false, "errors": errors.duplicate(), "timeline": ""}
	return {"ok": true, "errors": [], "timeline": "\n".join(lines) + "\n",
		"manifest": {"format": 1, "target": "Dialogic 2.0-alpha-20", "title": document.get("title", ""),
			"entry_label": _label(entry), "variable_defaults": defaults,
			"characters": characters, "node_labels": ids}}

func _emit_node(id: String, lines: Array[String]) -> void:
	var node: Dictionary = nodes[id]
	var data: Dictionary = node.get("data", {})
	var type = node.get("type", "")
	if type not in SUPPORTED:
		errors.append("Node %s: unsupported type '%s'" % [id, type])
		return
	var max_slot = 0
	match type:
		"content":
			if data.get("auto", false) or data.get("clear", false) or data.get("brief", 0) != 0:
				errors.append("Node %s: content presentation flags need an explicit mapping" % id)
			if data.get("title", "") != "":
				_emit_text(str(data.title), "_", lines)
			_emit_text(str(data.get("content", "")), "_", lines)
		"monolog":
			if data.get("auto", false) or data.get("clear", false) or data.get("brief", 0) != 0:
				errors.append("Node %s: monolog presentation flags need an explicit mapping" % id)
			_emit_text(str(data.get("monolog", "")), _speaker(data.get("character", -1)), lines)
		"dialog":
			var choices = data.get("lines", ["Hey there!"])
			if not choices is Array or choices.is_empty():
				errors.append("Node %s: dialog needs at least one line" % id)
				return
			max_slot = choices.size() - 1
			if data.get("playable", false):
				for i in choices.size():
					var text: String = choices[i]
					if not _safe_text(text) or "|" in text or "\n" in text:
						errors.append("Node %s: choice needs plain, single-line text without |" % id)
					lines.append("- " + text)
					lines.append("\t" + _next(id, i))
			else:
				if choices.size() != 1:
					errors.append("Node %s: NPC dialog randomly selects a line in Arrow; unsupported" % id)
				_emit_text(str(choices[0]), _speaker(data.get("character", -1)), lines)
		"condition", "variable_update":
			var variable = variables.get(str(int(data.get("variable", -1))), {})
			if variable.is_empty():
				errors.append("Node %s: missing variable" % id)
				return
			var operand = _operand(data, variable)
			var operator = str(data.get("operator", ""))
			if type == "condition":
				max_slot = 1
				if variable.type not in ["bool", "num"] or not COMPARISONS.has(operator) or (variable.type == "bool" and operator not in ["eq", "nq"]):
					errors.append("Node %s: unsupported comparison" % id)
					return
				lines.append("if {%s} %s %s:" % [variable.name, COMPARISONS[operator], operand])
				lines.append("\t" + _next(id, 1)) # Arrow TRUE=1, FALSE=0.
				lines.append("else:")
				lines.append("\t" + _next(id, 0))
			else:
				if operator != "set":
					errors.append("Node %s: only assignment is supported in v0.1" % id)
				lines.append("set {%s} = %s" % [variable.name, operand])
		"jump":
			var target = str(int(data.get("target", -1)))
			if not nodes.has(target):
				errors.append("Node %s: missing jump target %s" % [id, target])
			lines.append("jump " + _label(target))
	for slot in edges[id]:
		if slot > max_slot or type == "jump":
			errors.append("Node %s: unexpected outgoing slot %s" % [id, slot])
	if type not in ["condition", "jump"] and not (type == "dialog" and data.get("playable", false)):
		lines.append(_next(id, 0))

func _next(id: String, slot: int) -> String:
	if edges[id].has(slot):
		return "jump " + _label(edges[id][slot])
	return "[end_timeline]"

func _speaker(value: Variant) -> String:
	var id = str(int(value))
	if int(value) < 0:
		return "_"
	if not characters.get(id) is Dictionary:
		errors.append("Missing character %s" % id)
		return "_"
	# Explicit bindings are existing Dialogic character identifiers, not file paths.
	var name = str(bindings.get(id, characters[id].get("name", "")))
	if name.strip_edges().is_empty() or name != name.strip_edges() or name == "_" or "\r" in name or "\t" in name or "]" in name or "}" in name or '"' in name or ":" in name or "\n" in name or "{" in name or "/" in name or "\\" in name:
		errors.append("Invalid character identifier for %s" % id)
		return "_"
	return '"%s"' % name

func _emit_text(text: String, speaker: String, lines: Array[String]) -> void:
	if not _safe_text(text):
		errors.append("Text interpolation, markup and backslashes need an explicit mapping in v0.1")
		return
	for paragraph in text.split("\n"):
		if not paragraph.strip_edges().is_empty():
			lines.append(speaker + ": " + paragraph.replace(":", "\\:"))

func _safe_text(text: String) -> bool:
	return not text.is_empty() and not "{" in text and not "}" in text and not "[" in text and not "]" in text and not "\\" in text and not "\r" in text and not "\t" in text and not "<" in text and not ">" in text

func _operand(data: Dictionary, variable: Dictionary) -> String:
	var operand = data.get("with", [])
	if not operand is Array or operand.size() != 2:
		errors.append("Invalid variable operand")
		return "false"
	var value = operand[1]
	if operand[0] == 1:
		var other = variables.get(str(int(value)), {})
		if other.is_empty() or other.get("type") != variable.type:
			errors.append("Missing or mismatched operand variable")
			return "false"
		# Arrow treats a self-reference as its INITIAL value, not its current one.
		if str(int(value)) != str(int(data.variable)):
			return "{%s}" % other.name
		value = other.init
	elif operand[0] != 0:
		errors.append("Unknown operand mode")
	if not _literal_matches(value, variable.type):
		errors.append("Operand type does not match variable")
		return "false"
	if value is String and ('"' in value or "\\" in value or "\n" in value or "\r" in value or "\t" in value or "{" in value or "}" in value or "[" in value or "]" in value):
		errors.append("Unsupported characters in string assignment")
	return JSON.stringify(value)

func _literal_matches(value: Variant, type: Variant) -> bool:
	match type:
		"bool": return value is bool
		"str": return value is String
		"num": return (value is int or value is float) and is_finite(float(value)) and abs(value) <= MAX_EXACT_INTEGER and float(value) == floor(float(value))
	return false

func _identifier(value: Variant) -> bool:
	return value is String and value.is_valid_identifier() and RegEx.create_from_string("^[A-Za-z_][A-Za-z0-9_]*$").search(value) != null

func _valid_id(value: String) -> bool:
	return value.is_valid_int() and int(value) >= 0 and str(int(value)) == value

func _failure(message: String) -> Dictionary:
	errors.append(message)
	return {"ok": false, "errors": errors.duplicate(), "timeline": ""}

# JSON numbers arrive as floats. Never truncate fractional references or accept
# booleans/strings/containers as IDs. Larger numeric IDs cannot round-trip exactly.
func _integer(value: Variant, minimum: int = -1) -> bool:
	return (value is int or value is float) and is_finite(float(value)) and value >= minimum and value <= MAX_EXACT_INTEGER and value == floor(float(value))

func _validate_shape(document: Dictionary) -> bool:
	if not document.get("title", "") is String or not _integer(document.get("entry"), 0):
		return _shape_error("Project title must be text and entry must be an exact nonnegative integer")
	var resources = document.get("resources")
	if not resources is Dictionary:
		return _shape_error("resources must be an object")
	for collection in ["nodes", "scenes", "variables", "characters"]:
		if not resources.get(collection, {}) is Dictionary:
			return _shape_error("resources.%s must be an object" % collection)
	for id in resources.get("nodes", {}):
		var node = resources.nodes[id]
		if not node is Dictionary or not node.get("type") is String or not node.get("data") is Dictionary:
			return _shape_error("Node %s needs a type string and data object" % id)
		var data: Dictionary = node.data
		for field in ["title", "content", "monolog", "operator"]:
			if data.has(field) and not data[field] is String:
				return _shape_error("Node %s: %s must be text" % [id, field])
		for field in ["auto", "clear", "playable"]:
			if data.has(field) and not data[field] is bool:
				return _shape_error("Node %s: %s must be boolean" % [id, field])
		for field in ["character", "variable", "target", "brief"]:
			# Arrow uses -254 as its anonymous character sentinel.
			if data.has(field) and not _integer(data[field], -254 if field == "character" else -1):
				return _shape_error("Node %s: %s must be an exact integer" % [id, field])
		if node.type == "dialog":
			if not data.get("lines") is Array or data.lines.is_empty():
				return _shape_error("Node %s: dialog needs lines" % id)
			for line in data.lines:
				if not line is String or line.strip_edges().is_empty():
					return _shape_error("Node %s: dialog lines must be nonblank text" % id)
		if node.type in ["condition", "variable_update"]:
			var operand = data.get("with")
			if not operand is Array or operand.size() != 2 or not _integer(operand[0], 0) or (operand[0] != 0 and operand[0] != 1):
				return _shape_error("Node %s: invalid operand mode or shape" % id)
			if operand[0] == 1 and not _integer(operand[1], 0):
				return _shape_error("Node %s: operand variable must be an exact ID" % id)
	for id in resources.get("scenes", {}):
		var scene = resources.scenes[id]
		if not id is String or not _valid_id(id) or not scene is Dictionary or not scene.get("map") is Dictionary:
			return _shape_error("Invalid scene %s" % id)
		for placement in scene.map.values():
			if not placement is Dictionary or (placement.has("skip") and not placement.skip is bool):
				return _shape_error("Scene %s: invalid placement or skip flag" % id)
	for id in bindings:
		if not id is String or not _valid_id(id) or not bindings[id] is String or not resources.get("characters", {}).has(id):
			return _shape_error("Unknown or malformed character binding %s" % id)
	return true

func _shape_error(message: String) -> bool:
	errors.append(message)
	return false

func _label(id: String) -> Variant:
	return labels.get(id, "arrow_" + id)
