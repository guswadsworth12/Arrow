extends Node
# Test-only entrypoint; tools/export_pwa.py copies this only with --test-driver.
var main
var panel
var command_callback

func _ready() -> void:
	main = get_parent()
	await get_tree().process_frame
	await get_tree().process_frame
	main.UI.set_panel_visibility("about", false)
	main.open_story_service()
	panel = main.StoryService
	command_callback = JavaScriptBridge.create_callback(_command)
	JavaScriptBridge.get_interface("window").ArrowGodotCommand = command_callback
	JavaScriptBridge.eval("window.ArrowTest = {pending: {}, sequence: 0, send(data) {const id = ++this.sequence; data.id = id; return new Promise(resolve => {this.pending[id] = resolve; window.ArrowGodotCommand(JSON.stringify(data));});}, reply(id, value) {this.pending[id](JSON.parse(value)); delete this.pending[id];}};", true)

func _choice(choice: OptionButton, key: String) -> void:
	for index in choice.item_count:
		if choice.get_item_metadata(index) == key or choice.get_item_text(index) == key:
			choice.select(index)
			return

func _state() -> Dictionary:
	return {"menu": main.UI.MAIN_UI.app_menu._IDX.has("STORY_SERVICE"), "status": panel.status.text,
		"story": panel.snapshot.get("story_id", ""), "revision": panel.snapshot.get("revision", 0),
		"proposal": panel.reviewed.get("id", ""), "proposal_status": panel.reviewed.get("status", ""),
		"review": panel.review.text, "token_empty": panel.token.text.is_empty(),
		"local_id": main.Mind.ProMan.get_active_project_id(), "bindings": panel.bindings.size()}

func _command(args) -> void:
	var data = JSON.parse_string(str(args[0]))
	match data.action:
		"connect":
			panel.server.text = data.url
			panel.token.text = data.token
			await panel.connect_service()
		"choose_import":
			panel.message.text = "Import through the installed PWA"
			panel.choose_import()
		"load":
			_choice(panel.stories, data.story)
			await panel.load_story()
		"open":
			_choice(panel.chapters, "briefing")
			panel.open_chapter()
			await get_tree().process_frame
		"edit_propose":
			main.Mind.update_resource(2, {"data": {"content": "Edited in the PWA."}}, "nodes")
			main.Mind.reset_project_save_status(false)
			main.Mind.save_project()
			panel.message.text = "PWA graph edit"
			await panel.propose_current()
		"apply":
			await panel.apply_reviewed()
		"settings":
			panel.open_story_settings()
			var settings = panel.story_settings
			settings.character_name.text = "PWA Rend"
			settings.stage_character()
			settings.default_bool.button_pressed = true
			settings.stage_variable()
			_choice(settings.target_chapter, "followup")
			settings._fill_nodes(settings.target_node, "followup")
			_choice(settings.target_node, "2")
			settings.stage_link()
			settings.message.text = "PWA shared definitions and routing"
			await settings.propose()
		"story_export":
			panel.export_story(data.get("dialogic", false))
		"chapter_export":
			main.Mind.export_project_from_browser(data.format)
		"reopen_local":
			for key in panel.bindings:
				main.Mind.open_project(int(key))
				break
		"state":
			pass
	JavaScriptBridge.get_interface("ArrowTest").reply(data.id, JSON.stringify(_state()))
