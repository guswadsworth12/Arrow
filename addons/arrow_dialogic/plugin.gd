@tool
extends EditorPlugin

const Compiler = preload("compiler.gd")
const Story = preload("story.gd")
const Publisher = preload("publisher.gd")
var picker: EditorFileDialog
var report: AcceptDialog
var checking_dependencies: bool = false

func _enter_tree() -> void:
	picker = EditorFileDialog.new()
	picker.file_mode = EditorFileDialog.FILE_MODE_OPEN_FILE
	picker.access = EditorFileDialog.ACCESS_RESOURCES
	picker.filters = PackedStringArray(["*.arrow,*.json ; Arrow narrative document or story manifest"])
	picker.file_selected.connect(_select_source)
	get_editor_interface().get_base_control().add_child(picker)
	report = AcceptDialog.new()
	report.title = "Arrow to Dialogic"
	get_editor_interface().get_base_control().add_child(report)
	add_tool_menu_item("Import Arrow into Dialogic…", _open_picker)
	add_tool_menu_item("Check Arrow import dependencies…", _open_dependency_picker)

func _exit_tree() -> void:
	remove_tool_menu_item("Import Arrow into Dialogic…")
	remove_tool_menu_item("Check Arrow import dependencies…")
	picker.queue_free()
	report.queue_free()

func _open_picker() -> void:
	checking_dependencies = false
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
	if str(config.get_value("plugin", "version", "")).to_lower().strip_edges().get_slice(" ", 0) != "2.0-alpha-20":
		_show("This bridge targets Dialogic 2.0-alpha-20. Install that release before importing.")
		return
	var source_text = FileAccess.get_file_as_string(path)
	var json = JSON.new()
	if json.parse(source_text) != OK or not json.data is Dictionary:
		_show("Invalid Arrow JSON: " + json.get_error_message())
		return
	var is_story = json.data.get("format") is String and json.data.format == Story.FORMAT
	var settings_bindings = ProjectSettings.get_setting("arrow_dialogic/story_character_bindings" if is_story else "arrow_dialogic/character_bindings", {})
	if not settings_bindings is Dictionary:
		_show("Arrow character bindings must be a Dictionary")
		return
	var custom_bindings: Dictionary = {}
	var result: Dictionary
	var bindings: Dictionary = {}
	if is_story:
		# Story bindings use story-id/registry-key, independent of local Arrow UIDs.
		var scoped: Dictionary = {}
		var story_id = json.data.get("id", "")
		if not story_id is String:
			_show("Story id must be an identifier string")
			return
		for key in settings_bindings:
			if not key is String:
				_show("Story character binding keys must be story-id/registry-key strings")
				return
			if key.begins_with(story_id + "/"):
				scoped[key.trim_prefix(story_id + "/")] = settings_bindings[key]
		result = Story.new().compile(path, json.data, source_text, scoped)
		if result.ok:
			bindings = result.manifest.character_bindings
			for key in scoped:
				custom_bindings[result.manifest.registry_ids.characters[key]] = scoped[key]
	else:
		bindings = settings_bindings.duplicate()
		custom_bindings = bindings.duplicate()
		var resources = json.data.get("resources", {})
		if not resources is Dictionary or not resources.get("characters", {}) is Dictionary:
			_show("Arrow resources and characters must be objects")
			return
		for id in resources.get("characters", {}):
			if not bindings.has(id):
				bindings[id] = "arrow_character_%s_%s" % [path.sha256_text().left(8), id]
		result = Compiler.new().compile(json.data, bindings)
	if not result.ok:
		_show("Import stopped. No output was written.\n\n" + "\n".join(result.errors))
		return
	var chars: Dictionary = result.manifest.characters
	var util = load("res://addons/dialogic/Core/DialogicResourceUtil.gd")
	var character_script = load("res://addons/dialogic/Resources/character.gd")
	for setting in ["dialogic/variables", "dialogic/directories/dch_directory", "dialogic/directories/dtl_directory"]:
		if not ProjectSettings.get_setting(setting, {}) is Dictionary:
			_show(setting + " must be a Dictionary")
			return
	for id in custom_bindings:
		if not id is String or not chars.has(id) or not custom_bindings[id] is String or not is_instance_of(util.get_character_resource(custom_bindings[id]), character_script):
			_show("Unknown Dialogic character binding: " + str(id))
			return
	var existing_defaults = ProjectSettings.get_setting("dialogic/variables", {})
	if not existing_defaults is Dictionary:
		_show("dialogic/variables must be a Dictionary")
		return
	for name in result.manifest.variable_defaults:
		if existing_defaults.has(name) and not _same_type(existing_defaults[name], result.manifest.variable_defaults[name]):
			_show("Existing Dialogic variable has a different type: " + name)
			return
	var folder = _output_folder(path)
	var manifest_path = folder.path_join("manifest.json")
	if FileAccess.file_exists(manifest_path):
		var previous_manifest = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
		if not previous_manifest is Dictionary or not previous_manifest.get("source") is String or previous_manifest.source != path:
			_show("Output manifest belongs to another source or is invalid: " + manifest_path)
			return
	var files: Dictionary = {}
	var character_directory = util.get_directory("dch").duplicate()
	var timeline_directory = util.get_directory("dtl").duplicate()
	# Use registered identifiers, never display names, to resolve characters.
	for id in chars:
		if custom_bindings.has(id):
			continue
		var identifier: String = bindings[id]
		var character_path = folder.path_join(identifier + ".dch")
		if character_directory.has(identifier) and character_directory[identifier] != character_path:
			_show("Generated character identifier is already registered elsewhere: " + identifier)
			return
		if FileAccess.file_exists(character_path):
			if not is_instance_of(load(character_path), character_script):
				_show("Existing character stub is not a Dialogic character: " + character_path)
				return
		else:
			var character = character_script.new()
			character.display_name = chars[id].name
			files[character_path] = var_to_str(inst_to_dict(character))
		character_directory[identifier] = character_path
	var manifest: Dictionary = result.manifest
	manifest["source"] = path
	manifest["source_sha256"] = source_text.sha256_text()
	manifest["character_bindings"] = bindings
	files[folder.path_join("story.dtl")] = result.timeline
	files[folder.path_join("manifest.json")] = JSON.stringify(manifest, "\t")
	# Register a stable, unambiguous timeline alias rather than whichever story.dtl
	# happens to be discovered first during a global resource directory scan.
	var timeline_identifier = "arrow_story_" + path.sha256_text().left(8)
	if timeline_directory.has(timeline_identifier) and timeline_directory[timeline_identifier] != folder.path_join("story.dtl"):
		_show("Generated timeline identifier is already registered elsewhere: " + timeline_identifier)
		return
	timeline_directory[timeline_identifier] = folder.path_join("story.dtl")
	var defaults: Dictionary = existing_defaults.duplicate(true)
	for name in manifest.variable_defaults:
		if not defaults.has(name):
			defaults[name] = manifest.variable_defaults[name]
	# Stage project settings with the outputs; restore memory before publication.
	var updates = {"dialogic/variables": defaults, "dialogic/directories/dch_directory": character_directory,
		"dialogic/directories/dtl_directory": timeline_directory}
	var previous: Dictionary = {}
	for key in updates:
		previous[key] = ProjectSettings.get_setting(key)
		ProjectSettings.set_setting(key, updates[key])
	var settings_path = "user://arrow-settings-%s-%s.godot" % [OS.get_process_id(), Time.get_ticks_usec()]
	var status = ProjectSettings.save_custom(settings_path)
	for key in previous:
		ProjectSettings.set_setting(key, previous[key])
	if status != OK:
		_show("Could not stage project settings. No output was published.")
		return
	files["res://project.godot"] = FileAccess.get_file_as_string(settings_path)
	DirAccess.remove_absolute(settings_path)
	var stale = Story.dependency_changes(manifest)
	if not stale.is_empty():
		_show("Sources changed during import. No output was published. Reimport after saving:\n" + "\n".join(stale))
		return
	var publisher = _make_publisher()
	if not publisher.publish(files):
		_show("Import publication failed. Previous files were restored unless recovery errors follow.\n" + publisher.error)
		return
	for key in updates:
		ProjectSettings.set_setting(key, updates[key])
	Engine.set_meta("dch_directory", character_directory)
	Engine.set_meta("dtl_directory", timeline_directory)
	get_editor_interface().get_resource_filesystem().scan()
	_show("Imported into:\n" + folder + "\n\nRun: Dialogic.start(\"" + folder.path_join("story.dtl") + "\")\n\nExisting variable defaults and character stubs were preserved.\nGame save/load and quest systems remain owned by your game." + ("\n\nLinked chapters: %s. Entry labels and dependencies are in manifest.json." % manifest.chapter_entries.size() if manifest.has("chapter_entries") else "") + ("\n\nCleanup warning:\n" + publisher.error if not publisher.error.is_empty() else ""))

func _make_publisher() -> RefCounted:
	return Publisher.new()

func _same_type(left: Variant, right: Variant) -> bool:
	# Godot JSON reads all numbers as floats; authored Dialogic defaults can be ints.
	return typeof(left) == typeof(right) or ((left is int or left is float) and (right is int or right is float))

func _open_dependency_picker() -> void:
	checking_dependencies = true
	picker.popup_centered_ratio(0.7)

func _select_source(path: String) -> void:
	if checking_dependencies:
		_check_dependencies(path)
	else:
		_import_document(path)

func _check_dependencies(path: String) -> void:
	var manifest_path = _output_folder(path).path_join("manifest.json")
	if not FileAccess.file_exists(manifest_path):
		_show("No published import found for " + path)
		return
	var manifest = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	if not manifest is Dictionary or not manifest.get("source") is String or manifest.source != path:
		_show("Invalid import manifest: " + manifest_path)
		return
	var changes = Story.dependency_changes(manifest)
	_show("Import is up to date." if changes.is_empty() else "Import is stale. Reimport the source after reviewing:\n" + "\n".join(changes))

func _output_folder(path: String) -> String:
	return "res://generated/arrow/" + path.get_file().get_basename().validate_filename() + "_" + path.sha256_text().left(8)
