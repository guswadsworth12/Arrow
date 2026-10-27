@tool
extends "res://addons/arrow_dialogic/plugin.gd"

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
	var generated_before = FileAccess.get_file_as_string(folder.path_join("story.dtl"))
	var source = JSON.parse_string(FileAccess.get_file_as_string("res://example.arrow"))
	source.resources.nodes["2"].type = "randomizer"
	var broken_file = FileAccess.open("res://example.arrow", FileAccess.WRITE)
	broken_file.store_string(JSON.stringify(source))
	broken_file.close()
	_import_document("res://example.arrow")
	if not report.dialog_text.begins_with("Import stopped.") or FileAccess.get_file_as_string(folder.path_join("story.dtl")) != generated_before:
		printerr("Invalid import modified generated content")
		get_tree().quit(1)
		return
	print("Dialogic Alpha 20 editor importer: generated files, character load, repeat import and invalid-import preservation verified")
	get_tree().quit(0)
