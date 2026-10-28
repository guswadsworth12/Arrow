extends SceneTree

const Compiler = preload("res://addons/arrow_dialogic/compiler.gd")
class FailingPublisher extends "res://addons/arrow_dialogic/publisher.gd":
	var fail_at: int = -1
	var calls: int = 0
	func _rename(source: String, target: String) -> Error:
		calls += 1
		return ERR_CANT_CREATE if calls == fail_at else super._rename(source, target)

var failures: Array[String] = []
var checks = 0

func _initialize() -> void:
	var source = JSON.parse_string(FileAccess.get_file_as_string("res://example.arrow"))
	var compiler = Compiler.new()
	var result = compiler.compile(source)
	check(result.ok, "example compiles: " + str(result.errors))
	if result.ok:
		check("if {request_accepted} == true:\n\tjump arrow_8\nelse:\n\tjump arrow_9" in result.timeline, "true/false slot polarity")
		check(result.timeline.count("label arrow_7\n") == 1, "reconverged node emitted once")
		check("- Accept the request.\n\tjump arrow_4" in result.timeline, "choice target")
		check("jump arrow_12" in result.timeline, "cross-scene jump")
		check(not result.timeline.begins_with("set "), "does not reset variables on playback")
		check(result.manifest.variable_defaults.request_accepted == false, "defaults exported separately")
		check(result.timeline == compiler.compile(source).timeline, "deterministic compilation")
		var file = FileAccess.open("res://compiled.dtl", FileAccess.WRITE)
		file.store_string(result.timeline)
	var bad = source.duplicate(true)
	bad.resources.nodes["2"].type = "randomizer"
	check_rejected(compiler.compile(bad), "unsupported node")
	bad = source.duplicate(true)
	bad.resources.scenes["0"].map["1"].io[0][2] = 999
	check_rejected(compiler.compile(bad), "dangling edge")
	bad = source.duplicate(true)
	bad.resources.scenes["0"].map["1"].io.append([1, 0, 3, 0])
	check_rejected(compiler.compile(bad), "ambiguous outgoing edge")
	bad = source.duplicate(true)
	bad.resources.nodes["4"].data.with = [0, "true"]
	check_rejected(compiler.compile(bad), "wrong operand type")
	bad = source.duplicate(true)
	bad.resources.nodes["3"].data.playable = false
	check_rejected(compiler.compile(bad), "random NPC alternatives")
	bad = source.duplicate(true)
	bad.resources.nodes["2"].data.content = "[signal arg=evil]"
	check_rejected(compiler.compile(bad), "unmapped markup")
	bad = source.duplicate(true)
	bad.resources.nodes["4"].data.with = [1, 200]
	var self_reference = compiler.compile(bad)
	check(self_reference.ok and "set {request_accepted} = false" in self_reference.timeline, "self-reference means initial value")
	bad = source.duplicate(true)
	bad.resources.nodes["2"].data.content = "jump arrow_999: this is prose."
	var prose = compiler.compile(bad)
	check(prose.ok and "_: jump arrow_999\\: this is prose." in prose.timeline, "prose cannot become an instruction")
	check_rejected(compiler.compile({}), "empty document")
	check_rejected(compiler.compile({"resources": []}), "malformed resource collection")
	# Malformed JSON must return diagnostics, never trigger GDScript conversion errors.
	for wire_value in [null, true, "2", 2.5, [], {}, 9007199254740992.0]:
		for index in 4:
			bad = source.duplicate(true)
			bad.resources.scenes["0"].map["1"].io[0][index] = wire_value
			check_rejected(compiler.compile(bad), "malformed wire component %s: %s" % [index, wire_value])
	for value in [null, true, "1", 1.5, [], {}]:
		bad = source.duplicate(true)
		bad.entry = value
		check_rejected(compiler.compile(bad), "malformed project entry %s" % [value])
		for field in ["variable", "with", "operator"]:
			bad = source.duplicate(true)
			bad.resources.nodes["4"].data[field] = value
			check_rejected(compiler.compile(bad), "malformed update %s: %s" % [field, value])
		for field in ["target", "character", "content", "auto", "brief"]:
			bad = source.duplicate(true)
			var id = "10" if field == "target" else ("5" if field == "character" else "2")
			bad.resources.nodes[id].data[field] = value
			# A boolean is valid only for auto; an integer is not in this matrix.
			if not ((field == "auto" and value is bool) or (field == "content" and value is String)):
				check_rejected(compiler.compile(bad), "malformed field %s: %s" % [field, value])
	for value in [null, [], {}, true, "num"]:
		bad = source.duplicate(true)
		bad.resources.variables["200"].type = value
		check_rejected(compiler.compile(bad), "malformed variable type %s" % [value])
	for value in [[{}, "choice"], [null], ["   "], [], "choices"]:
		bad = source.duplicate(true)
		bad.resources.nodes["3"].data.lines = value
		check_rejected(compiler.compile(bad), "malformed choice text")
	for value in [null, true, "1", 0.5, [], {}]:
		bad = source.duplicate(true)
		bad.resources.nodes["4"].data.with = [value, true]
		check_rejected(compiler.compile(bad), "malformed operand mode")
		bad.resources.nodes["4"].data.with = [1, value]
		check_rejected(compiler.compile(bad), "malformed operand reference")
	bad = source.duplicate(true)
	bad.entry = 2
	check_rejected(compiler.compile(bad), "project entry must be entry node")
	bad = source.duplicate(true)
	bad.resources.variables["bad-id"] = bad.resources.variables["200"]
	check_rejected(compiler.compile(bad), "invalid variable ID")
	bad = source.duplicate(true)
	bad.resources.scenes["0"].map["1"].skip = "false"
	check_rejected(compiler.compile(bad), "skip must be boolean")
	for identifier in ["_", " Rend", "Rend\r", "Rend\t", "Rend}Injected"]:
		check_rejected(compiler.compile(source, {"100": identifier}), "invalid speaker identifier")
	check_rejected(compiler.compile(source, {"999": "Unknown"}), "unknown binding ID")
	bad = source.duplicate(true)
	bad.resources.nodes["2"].data.content = "Pick <one/two>."
	check_rejected(compiler.compile(bad), "Dialogic random text syntax must not reinterpret Arrow prose")
	bad = source.duplicate(true)
	bad.resources.scenes["0"].entry = 12
	check_rejected(compiler.compile(bad), "scene entry must belong to that scene")
	bad = source.duplicate(true)
	bad.resources.scenes["0"].macro = true
	check_rejected(compiler.compile(bad), "macro scene unsupported")
	bad = source.duplicate(true)
	bad.resources.characters["100"].erase("name")
	check_rejected(compiler.compile(bad), "missing character display name")
	# Test the plugin script parses even without Dialogic installed.
	var plugin_script = load("res://addons/arrow_dialogic/plugin.gd")
	check(plugin_script != null and plugin_script.can_instantiate(), "EditorPlugin parses")
	# A handled publication failure must preserve every old file, not only timeline.
	var output = "res://publisher-test/"
	DirAccess.make_dir_recursive_absolute(output)
	var old = {output + "story.dtl": "old story", output + "manifest.json": "old manifest", output + "project.godot": "old settings"}
	var files = old.duplicate()
	files[output + "character.dch"] = "new character"
	for path in old:
		files[path] = "new " + old[path]
	# Three existing files each need backup+publish, followed by a new character.
	for failure_index in range(1, 8):
		for path in old:
			var file = FileAccess.open(path, FileAccess.WRITE)
			file.store_string(old[path])
			file.close()
		var publisher = FailingPublisher.new()
		publisher.fail_at = failure_index
		check(not publisher.publish(files), "publication failure injected at %s" % failure_index)
		for path in old:
			check(FileAccess.get_file_as_string(path) == old[path], "rollback preserved " + path)
		check(not FileAccess.file_exists(output + "character.dch"), "rollback removed newly created character")
	var publisher = FailingPublisher.new()
	check(publisher.publish(files), "successful publication")
	for path in files:
		check(FileAccess.get_file_as_string(path) == files[path], "published " + path)
	var blocked = output + "blocked.dch"
	DirAccess.make_dir_recursive_absolute(blocked)
	var before = FileAccess.get_file_as_string(output + "story.dtl")
	check(not publisher.publish({output + "story.dtl": "should not publish", blocked: "blocked"}), "staging failure rejects a directory target")
	check(FileAccess.get_file_as_string(output + "story.dtl") == before, "staging failure preserved existing output")
	check(DirAccess.get_files_at(output).size() == 4, "no leftover stage or backup files")
	print("Bridge compiler: %s checks, %s failures" % [checks, failures.size()])
	for failure in failures:
		printerr(failure)
	quit(0 if failures.is_empty() else 1)

func check_rejected(result: Dictionary, message: String) -> void:
	check(not result.ok and result.timeline == "" and not result.errors.is_empty(), message)

func check(condition: bool, message: String) -> void:
	checks += 1
	if not condition:
		failures.append(message)
