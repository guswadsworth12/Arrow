@tool
extends "res://addons/arrow_dialogic/plugin.gd"
const PATH = "res://story/guild_story.json"

class FailingPublisher extends "res://addons/arrow_dialogic/publisher.gd":
	var calls: int = 0
	func _rename(source: String, target: String) -> Error:
		calls += 1
		return ERR_CANT_CREATE if calls == 4 else super._rename(source, target)

var fail_publication: bool = false

func _make_publisher() -> RefCounted:
	return FailingPublisher.new() if fail_publication else super._make_publisher()

func _enter_tree() -> void:
	super._enter_tree()
	call_deferred("_test_story")

func _test_story() -> void:
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	var folder = _output_folder(PATH)
	var manifest_path = folder.path_join("manifest.json")
	var manifest = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	assert(manifest.dependencies.size() == 3 and manifest.document_labels.size() == 2)
	assert(manifest.registry_ids.characters.size() == 1)
	var character_id = manifest.registry_ids.characters.rend
	var stub_path = folder.path_join(manifest.character_bindings[character_id] + ".dch")
	var character = load(stub_path)
	assert(character != null and character.display_name == "Rend")
	var original_stub = FileAccess.get_file_as_string(stub_path)
	var defaults: Dictionary = ProjectSettings.get_setting("dialogic/variables").duplicate(true)
	defaults.request_accepted = true
	ProjectSettings.set_setting("dialogic/variables", defaults)
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	assert(ProjectSettings.get_setting("dialogic/variables").request_accepted == true)
	assert(FileAccess.get_file_as_string(stub_path) == original_stub)
	_check_dependencies(PATH)
	assert(report.dialog_text == "Import is up to date.", report.dialog_text)
	var before: Dictionary = {}
	for path in [manifest_path, folder.path_join("story.dtl"), stub_path, "res://project.godot"]:
		before[path] = FileAccess.get_file_as_string(path)
	# Editing any chapter makes the package stale. Invalid content cannot replace
	# any published file or settings, even when the selected manifest is unchanged.
	var chapter_path = "res://story/followup.arrow"
	var chapter_text = FileAccess.get_file_as_string(chapter_path)
	var chapter = JSON.parse_string(chapter_text)
	chapter.resources.variables["901"].init = true
	_write_source(chapter_path, JSON.stringify(chapter))
	_check_dependencies(PATH)
	assert(report.dialog_text.begins_with("Import is stale.") and chapter_path in report.dialog_text)
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Import stopped."), report.dialog_text)
	for path in before:
		assert(FileAccess.get_file_as_string(path) == before[path], "Invalid package changed " + path)
	# A valid revision still rolls back the entire package on a publication error.
	chapter = JSON.parse_string(chapter_text)
	chapter.resources.nodes["2"].data.monolog = "A revised chapter begins."
	_write_source(chapter_path, JSON.stringify(chapter))
	fail_publication = true
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Import publication failed."), report.dialog_text)
	for path in before:
		assert(FileAccess.get_file_as_string(path) == before[path], "Package rollback changed " + path)
	assert(ProjectSettings.get_setting("dialogic/variables").request_accepted == true)
	fail_publication = false
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	assert("A revised chapter begins." in FileAccess.get_file_as_string(folder.path_join("story.dtl")))
	_check_dependencies(PATH)
	assert(report.dialog_text == "Import is up to date.")
	# Story-scoped bindings resolve one registered game character in both chapters.
	var util = load("res://addons/dialogic/Core/DialogicResourceUtil.gd")
	var directory = util.get_directory("dch").duplicate()
	directory["existing_Rend"] = stub_path
	util.set_directory("dch", directory)
	ProjectSettings.set_setting("arrow_dialogic/story_character_bindings", {"jade_lantern_demo/rend": "existing_Rend", "unrelated_story/rend": "not_registered"})
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	var timeline = load("res://addons/dialogic/Resources/timeline.gd").new()
	timeline.from_text(FileAccess.get_file_as_string(folder.path_join("story.dtl")))
	timeline.process()
	var spoken = 0
	for event in timeline.events:
		if event.get_script() == load("res://addons/dialogic/Modules/Text/event_text.gd") and (event.text == "Who posted this request?" or event.text == "A revised chapter begins."):
			assert(event.character.resource_path == stub_path, "Shared character resolved to a fallback")
			spoken += 1
	assert(spoken == 2)
	# Leave a pristine package import for the subsequent real-playback test.
	_write_source(chapter_path, chapter_text)
	ProjectSettings.set_setting("arrow_dialogic/story_character_bindings", {})
	_import_document(PATH)
	assert(report.dialog_text.begins_with("Imported into:"), report.dialog_text)
	print("Story package importer: shared character/defaults, dependency status, changed chapter reimport, invalid-input preservation and rollback verified")
	get_tree().quit(0)

func _write_source(path: String, content: String) -> void:
	var file = FileAccess.open(path, FileAccess.WRITE)
	file.store_string(content)
	file.close()
