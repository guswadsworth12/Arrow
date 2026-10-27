@tool
extends EditorPlugin

const Compiler = preload("compiler.gd")
var picker: EditorFileDialog
var report: AcceptDialog

func _enter_tree() -> void:
	picker = EditorFileDialog.new()
	picker.file_mode = EditorFileDialog.FILE_MODE_OPEN_FILE
	picker.access = EditorFileDialog.ACCESS_RESOURCES
	picker.filters = PackedStringArray(["*.arrow,*.json ; Arrow narrative document"])
	picker.file_selected.connect(_import_document)
	get_editor_interface().get_base_control().add_child(picker)
	report = AcceptDialog.new()
	report.title = "Arrow to Dialogic"
	get_editor_interface().get_base_control().add_child(report)
	add_tool_menu_item("Import Arrow into Dialogic…", _open_picker)

func _exit_tree() -> void:
	remove_tool_menu_item("Import Arrow into Dialogic…")
	picker.queue_free()
	report.queue_free()

func _open_picker() -> void:
	picker.popup_centered_ratio(0.7)

func _show(message: String) -> void:
	report.dialog_text = message
	report.popup_centered(Vector2i(780, 420))

func _import_document(path: String) -> void:
	if not FileAccess.file_exists("res://addons/dialogic/plugin.cfg") or not ProjectSettings.has_setting("autoload/Dialogic"):
		_show("Install and enable Dialogic 2.0-alpha-20 before importing.")
		return
	var config = ConfigFile.new()
	config.load("res://addons/dialogic/plugin.cfg")
	if str(config.get_value("plugin", "version", "")).to_lower().begins_with("2.0-alpha-20") == false:
		_show("This bridge targets Dialogic 2.0-alpha-20. Install that release before importing.")
		return
	var json = JSON.new()
	if json.parse(FileAccess.get_file_as_string(path)) != OK or not json.data is Dictionary:
		_show("Invalid Arrow JSON: " + json.get_error_message())
		return
	var settings_bindings = ProjectSettings.get_setting("arrow_dialogic/character_bindings", {})
	if not settings_bindings is Dictionary:
		_show("arrow_dialogic/character_bindings must be a Dictionary")
		return
	var bindings: Dictionary = settings_bindings.duplicate()
	var custom_bindings = bindings.duplicate()
	var util = load("res://addons/dialogic/Core/DialogicResourceUtil.gd")
	for id in custom_bindings:
		if not custom_bindings[id] is String or util.get_character_resource(custom_bindings[id]) == null:
			_show("Unknown Dialogic character binding: " + str(id))
			return
	var resources = json.data.get("resources", {})
	if not resources is Dictionary:
		_show("resources must be an object")
		return
	var chars = resources.get("characters", {})
	if not chars is Dictionary:
		_show("resources.characters must be an object")
		return
	for id in chars:
		if not bindings.has(id):
			bindings[id] = "arrow_character_%s_%s" % [path.sha256_text().left(8), id]
	var result = Compiler.new().compile(json.data, bindings)
	if not result.ok:
		_show("Import stopped. No output was written.\n\n" + "\n".join(result.errors))
		return
	var existing_defaults: Dictionary = ProjectSettings.get_setting("dialogic/variables", {})
	for name in result.manifest.variable_defaults:
		if existing_defaults.has(name) and typeof(existing_defaults[name]) != typeof(result.manifest.variable_defaults[name]):
			_show("Existing Dialogic variable has a different type: " + name)
			return
	var folder = "res://generated/arrow/" + path.get_file().get_basename().validate_filename() + "_" + path.sha256_text().left(8)
	if DirAccess.make_dir_recursive_absolute(folder) != OK:
		_show("Could not create output directory: " + folder)
		return
	# Character stubs are created once; later imports preserve portraits and styling.
	var character_script = load("res://addons/dialogic/Resources/character.gd")
	for id in chars:
		if custom_bindings.has(id):
			continue
		var character_path = folder.path_join("arrow_character_%s_%s.dch" % [path.sha256_text().left(8), id])
		if not FileAccess.file_exists(character_path):
			var character = character_script.new()
			character.display_name = str(chars[id].get("name", ""))
			if not _write(character_path, var_to_str(inst_to_dict(character))):
				return
	var manifest: Dictionary = result.manifest
	manifest["source"] = path
	manifest["source_sha256"] = FileAccess.get_file_as_string(path).sha256_text()
	manifest["character_bindings"] = bindings
	if not _write(folder.path_join("story.dtl"), result.timeline):
		return
	if not _write(folder.path_join("manifest.json"), JSON.stringify(manifest, "\t")):
		return
	# Add missing defaults only; importing never resets existing authored defaults.
	var defaults: Dictionary = ProjectSettings.get_setting("dialogic/variables", {}).duplicate(true)
	for name in manifest.variable_defaults:
		if not defaults.has(name):
			defaults[name] = manifest.variable_defaults[name]
	ProjectSettings.set_setting("dialogic/variables", defaults)
	if ProjectSettings.save() != OK:
		_show("Files written, but project settings could not be saved. Add variable defaults manually.")
		return
	util.update_directory("dch")
	util.update_directory("dtl")
	get_editor_interface().get_resource_filesystem().scan()
	_show("Imported into:\n" + folder + "\n\nRun: Dialogic.start(\"" + folder.path_join("story.dtl") + "\")\n\nExisting variable defaults and character stubs were preserved.\nGame save/load and quest systems remain owned by your game.")

func _write(path: String, content: String) -> bool:
	var temporary = path + ".tmp"
	var file = FileAccess.open(temporary, FileAccess.WRITE)
	if file == null:
		_show("Cannot write " + temporary)
		return false
	file.store_string(content)
	file.close()
	if DirAccess.rename_absolute(temporary, path) != OK:
		_show("Cannot replace " + path)
		return false
	return true
