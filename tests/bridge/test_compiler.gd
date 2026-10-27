extends SceneTree

const Compiler = preload("res://addons/arrow_dialogic/compiler.gd")
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
	# Test the plugin script parses even without Dialogic installed.
	var plugin_script = load("res://addons/arrow_dialogic/plugin.gd")
	check(plugin_script != null and plugin_script.can_instantiate(), "EditorPlugin parses")
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
