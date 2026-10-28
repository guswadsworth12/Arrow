extends SceneTree
var seen: Array[String] = []
var speakers: Array[String] = []
var ended = false
func _initialize() -> void:
	call_deferred("run_tests")
func run_tests() -> void:
	var handler = root.get_node("Dialogic")
	var settings = ConfigFile.new()
	assert(settings.load("res://project-snippet.godot") == OK)
	for key in settings.get_section_keys("dialogic"):
		ProjectSettings.set_setting("dialogic/" + key, settings.get_value("dialogic", key))
	Engine.remove_meta("dch_directory")
	Engine.remove_meta("dtl_directory")
	var aliases: Dictionary = ProjectSettings.get_setting("dialogic/directories/dch_directory")
	for alias in aliases:
		var character = load(aliases[alias])
		assert(character != null and character.display_name == "PWA Rend")
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://dialogic_export/pwa_demo/manifest.json"))
	assert(load("res://addons/arrow_dialogic/story.gd").dependency_changes(manifest).is_empty())
	handler.Text.text_finished.connect(func(info):
		seen.append(info.text)
		if info.character:
			var character = DialogicResourceUtil.get_character_resource(info.character)
			assert(character != null and character.display_name == "PWA Rend")
			speakers.append(character.resource_path))
	handler.timeline_ended.connect(func(): ended = true)
	var timeline = "res://dialogic_export/pwa_demo/story.dtl"
	for option in [1, 2]:
		handler.VAR.var_storage = {"request_accepted": false}
		seen.clear(); speakers.clear(); ended = false
		handler.start(timeline)
		var chosen = false
		var deadline = Time.get_ticks_msec() + 20000
		while not ended and Time.get_ticks_msec() < deadline:
			await create_timer(0.05).timeout
			if handler.current_state == handler.States.REVEALING_TEXT:
				handler.Text.skip_text_reveal()
			elif handler.current_state == handler.States.AWAITING_CHOICE and not chosen:
				if not handler.Choices._choice_blocker.is_stopped():
					continue
				handler.Choices.select_choice(option)
				chosen = true
			elif handler.current_state == handler.States.IDLE and not seen.is_empty():
				handler.Inputs.dialogic_action.emit()
		assert(ended and chosen and not speakers.is_empty(), "Playback stalled: ended=%s chosen=%s state=%s event=%s seen=%s speakers=%s" % [ended, chosen, handler.current_state, handler.current_event_idx, seen, speakers])
		assert(seen.has("Edited in the PWA."))
		assert(seen.has("A new chapter begins."))
		assert(seen.has("Your accepted request carries into this chapter.") if option == 1 else seen.has("The request is still undecided in this chapter."))
	print("Downloaded PWA Dialogic export: both branches, shared speaker, variable state and source dependencies verified")
	quit(0)
