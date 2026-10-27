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
const COMPARISONS = {"eq": "==", "nq": "!=", "gt": ">", "gte": ">=", "ls": "<", "lse": "<="}
const SUPPORTED = ["entry", "content", "monolog", "dialog", "condition", "variable_update", "jump"]

func compile(document: Dictionary, character_bindings: Dictionary = {}) -> Dictionary:
	errors.clear()
	edges = {}
	bindings = character_bindings
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
		if not _valid_id(str(id)) or not nodes[id] is Dictionary:
			return _failure("Invalid node ID or object: %s" % id)
		if not nodes[id].get("data", {}) is Dictionary:
			return _failure("Node %s data must be an object" % id)
		edges[str(id)] = {}
	for id in characters:
		if not _valid_id(str(id)) or not characters[id] is Dictionary or not characters[id].get("name", "") is String:
			return _failure("Invalid character %s" % id)
	for id in variables:
		var item = variables[id]
		if not item is Dictionary or not _identifier(item.get("name", "")):
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
				if not wire is Array or wire.size() != 4:
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
	if not nodes.has(entry):
		return _failure("Missing project entry")
	var lines: Array[String] = ["# Generated from Arrow; edit the .arrow source.", "jump arrow_%s" % entry]
	var ids = nodes.keys()
	ids.sort_custom(func(a, b): return int(a) < int(b))
	for id in ids:
		lines.append("label arrow_%s" % id)
		_emit_node(str(id), lines)
	# Every block ends in a jump or end_timeline: no accidental fallthrough.
	var defaults: Dictionary = {}
	for item in variables.values():
		defaults[item.name] = item.init
	if not errors.is_empty():
		return {"ok": false, "errors": errors.duplicate(), "timeline": ""}
	return {"ok": true, "errors": [], "timeline": "\n".join(lines) + "\n",
		"manifest": {"format": 1, "target": "Dialogic 2.0-alpha-20", "title": document.get("title", ""),
			"entry_label": "arrow_%s" % entry, "variable_defaults": defaults,
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
					var text = str(choices[i])
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
			lines.append("jump arrow_%s" % target)
	for slot in edges[id]:
		if slot > max_slot or type == "jump":
			errors.append("Node %s: unexpected outgoing slot %s" % [id, slot])
	if type not in ["condition", "jump"] and not (type == "dialog" and data.get("playable", false)):
		lines.append(_next(id, 0))

func _next(id: String, slot: int) -> String:
	if edges[id].has(slot):
		return "jump arrow_%s" % edges[id][slot]
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
	if name.is_empty() or '"' in name or ":" in name or "\n" in name or "{" in name or "/" in name or "\\" in name:
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
	return not text.is_empty() and not "{" in text and not "}" in text and not "[" in text and not "]" in text and not "\\" in text and not "\r" in text and not "\t" in text

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
	if value is String and ('"' in value or "\\" in value or "\n" in value or "{" in value or "[" in value):
		errors.append("Unsupported characters in string assignment")
	return JSON.stringify(value)

func _literal_matches(value: Variant, type: String) -> bool:
	match type:
		"bool": return value is bool
		"str": return value is String
		"num": return (value is int or value is float) and is_finite(float(value)) and float(value) == floor(float(value))
	return false

func _identifier(value: Variant) -> bool:
	return value is String and value.is_valid_identifier()

func _valid_id(value: String) -> bool:
	return value.is_valid_int() and int(value) >= 0 and str(int(value)) == value

func _failure(message: String) -> Dictionary:
	errors.append(message)
	return {"ok": false, "errors": errors.duplicate(), "timeline": ""}
