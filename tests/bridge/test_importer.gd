@tool
extends "res://addons/arrow_dialogic/plugin.gd"

class FailingPublisher extends "res://addons/arrow_dialogic/publisher.gd":
	var calls: int = 0
	var fail_at: int = -1
	func _rename(source: String, target: String) -> Error:
		calls += 1
		return ERR_CANT_CREATE if calls == fail_at else super._rename(source, target)

var publication_failure: int = -1

func _make_publisher() -> RefCounted:
	var publisher = FailingPublisher.new()
	publisher.fail_at = publication_failure
	return publisher

func _enter_tree() -> void:
	super._enter_tree()
	call_deferred("_test_import")

func _test_import() -> void:
	_import_document("res://example.arrow")
	if not report.dialog_text.begins_with("Imported into:"):
		printerr("Importer smoke test failed: " + report.dialog_text)
		get_tree().quit(1)
		return
	var folder = "res://generated/arrow/example_" + "res://example.arrow".sha256_text().left(8)
	var manifest = JSON.parse_string(FileAccess.get_file_as_string(folder.path_join("manifest.json")))
	var stub_path = folder.path_join(str(manifest.character_bindings["100"]) + ".dch")
	if not FileAccess.file_exists(folder.path_join("story.dtl")) or not FileAccess.file_exists(stub_path):
		printerr("Importer failed to write its timeline or character resource")
		get_tree().quit(1)
		return
	var character = load(stub_path)
	if character == null or character.display_name != "Rend":
		printerr("Generated character resource did not load")
		get_tree().quit(1)
		return
	var original = FileAccess.get_file_as_string(stub_path)
	# Existing authored defaults must survive another import.
	var defaults: Dictionary = ProjectSettings.get_setting("dialogic/variables", {})
	defaults.request_accepted = true
	ProjectSettings.set_setting("dialogic/variables", defaults)
	_import_document("res://example.arrow")
	if not report.dialog_text.begins_with("Imported into:") or FileAccess.get_file_as_string(stub_path) != original or ProjectSettings.get_setting("dialogic/variables").request_accepted != true:
		printerr("Reimport failed or replaced existing characters/defaults")
		get_tree().quit(1)
		return
	# Numeric defaults use one semantic type across Godot JSON floats and authored ints.
	var source_text = FileAccess.get_file_as_string("res://example.arrow")
	var source = JSON.parse_string(source_text)
	source.resources.variables["201"] = {"name": "visits", "type": "num", "init": 1}
	var edited = FileAccess.open("res://example.arrow", FileAccess.WRITE)
	edited.store_string(JSON.stringify(source))
	edited.close()
	defaults.visits = 4
	ProjectSettings.set_setting("dialogic/variables", defaults)
	_import_document("res://example.arrow")
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	assert(ProjectSettings.get_setting("dialogic/variables").visits == 4)
	# Character identifiers resolve to an existing resource, independently of display name.
	var util = load("res://addons/dialogic/Core/DialogicResourceUtil.gd")
	var character_directory = util.get_directory("dch").duplicate()
	character_directory["bound_Rend"] = stub_path
	util.set_directory("dch", character_directory)
	ProjectSettings.set_setting("arrow_dialogic/character_bindings", {"100": "bound_Rend"})
	_import_document("res://example.arrow")
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	assert('"bound_Rend": Who posted this request?' in FileAccess.get_file_as_string(folder.path_join("story.dtl")))
	var timeline = load("res://addons/dialogic/Resources/timeline.gd").new()
	timeline.from_text(FileAccess.get_file_as_string(folder.path_join("story.dtl")))
	timeline.process()
	var found_speaker = false
	for event in timeline.events:
		if event.get_script() == load("res://addons/dialogic/Modules/Text/event_text.gd") and event.text == "Who posted this request?":
			assert(event.character.resource_path == stub_path, "Bound speaker resolved to a fallback")
			found_speaker = true
	assert(found_speaker)
	ProjectSettings.set_setting("arrow_dialogic/character_bindings", {"999": "bound_Rend"})
	var before_invalid_binding = FileAccess.get_file_as_string(folder.path_join("story.dtl"))
	_import_document("res://example.arrow")
	assert(not report.dialog_text.begins_with("Imported into:"))
	assert(FileAccess.get_file_as_string(folder.path_join("story.dtl")) == before_invalid_binding)
	ProjectSettings.set_setting("arrow_dialogic/character_bindings", {})
	_import_document("res://example.arrow")
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	# Fail each backup/replace operation, including project settings. Memory and all
	# published outputs must retain the same state, not a mixture of revisions.
	var previous_files = {}
	for path in [folder.path_join("story.dtl"), folder.path_join("manifest.json"), stub_path, "res://project.godot"]:
		previous_files[path] = FileAccess.get_file_as_string(path)
	source.resources.nodes["2"].data.content = "A revised request."
	edited = FileAccess.open("res://example.arrow", FileAccess.WRITE)
	edited.store_string(JSON.stringify(source))
	edited.close()
	for failure_index in range(1, 7):
		publication_failure = failure_index
		_import_document("res://example.arrow")
		assert(report.dialog_text.begins_with("Import publication failed."), report.dialog_text)
		for path in previous_files:
			assert(FileAccess.get_file_as_string(path) == previous_files[path], "Partial publication at " + path)
		assert(ProjectSettings.get_setting("dialogic/variables").visits == 4)
	publication_failure = -1
	edited = FileAccess.open("res://example.arrow", FileAccess.WRITE)
	edited.store_string(source_text)
	edited.close()
	var generated_before = FileAccess.get_file_as_string(folder.path_join("story.dtl"))
	source = JSON.parse_string(FileAccess.get_file_as_string("res://example.arrow"))
	source.resources.nodes["2"].type = "randomizer"
	var broken_file = FileAccess.open("res://example.arrow", FileAccess.WRITE)
	broken_file.store_string(JSON.stringify(source))
	broken_file.close()
	_import_document("res://example.arrow")
	if not report.dialog_text.begins_with("Import stopped.") or FileAccess.get_file_as_string(folder.path_join("story.dtl")) != generated_before:
		printerr("Invalid import modified generated content")
		get_tree().quit(1)
		return
	print("Dialogic Alpha 20 editor importer: generated files, character load, repeat import, character bindings, defaults, publication rollback and invalid-import preservation verified")
	get_tree().quit(0)
