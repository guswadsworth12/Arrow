extends SceneTree
var main
var panel
var settings

func _initialize() -> void:
	create_timer(65).timeout.connect(func(): push_error("Story settings test timed out"); quit(1))
	call_deferred("run_tests")

func choose(choice: OptionButton, value: String) -> void:
	for index in choice.item_count:
		if choice.get_item_metadata(index) == value:
			choice.select(index)
			return
	assert(false, "Missing choice: " + value)

func screenshot(name: String) -> void:
	if DisplayServer.get_name() != "headless":
		await create_timer(0.2).timeout
		RenderingServer.force_draw()
		settings.get_texture().get_image().save_png(OS.get_environment("ARROW_TEST_OUTPUT").get_base_dir().path_join(name + ".png"))

func write_result(value: Dictionary) -> void:
	var file = FileAccess.open(OS.get_environment("ARROW_TEST_OUTPUT"), FileAccess.WRITE)
	file.store_string(JSON.stringify(value))
	file.close()

func run_tests() -> void:
	main = load("res://main.tscn").instantiate()
	root.add_child(main)
	await process_frame
	main.open_story_service()
	panel = main.StoryService
	panel.server.text = OS.get_environment("ARROW_STORY_URL")
	panel.token.text = OS.get_environment("ARROW_STORY_API_TOKEN")
	await panel.connect_service()
	choose(panel.stories, OS.get_environment("ARROW_TEST_STORY"))
	await panel.load_story()
	panel.open_story_settings()
	settings = panel.story_settings
	assert(settings.draft.source.revision == 1)
	if OS.get_environment("ARROW_TEST_MODE") == "empty-settings":
		assert(settings.character.item_count == 0)
		assert(settings.variable.item_count == 0)
		assert(settings.source_jump.item_count == 0)
		assert(settings.current_link.text.contains("no Jump nodes"))
		settings.stage_character()
		settings.stage_variable()
		settings.stage_link()
		assert(settings.draft.groups.is_empty())
		write_result({"empty": true})
		print("Arrow service UI: PASS — empty shared registries and chapters without Jump nodes")
		quit(0)
		return
	# Reverting to a loaded value removes that staged edit; replacing a value does not duplicate it.
	settings.character_name.text = "Temporary name"
	settings.stage_character()
	assert(settings.draft.groups.size() == 1)
	settings.character_name.text = "Rend"
	settings.stage_character()
	assert(settings.draft.groups.is_empty())
	settings.character_name.text = "Rend of the Guild"
	settings.stage_character()
	settings.character_name.text = "Rend of the Lantern Guild"
	settings.stage_character()
	assert(settings.draft.groups.size() == 1)
	assert(settings.character_usage.text.contains("briefing") and settings.character_usage.text.contains("followup"))
	choose(settings.variable, "request_accepted")
	settings._refresh_variable()
	assert(settings.default_bool.visible)
	settings.default_bool.button_pressed = true
	settings.stage_variable()
	choose(settings.variable, "counter")
	settings._refresh_variable()
	assert(settings.default_text.visible and not settings.default_bool.visible)
	for invalid in ["1.5", "9007199254740992", "-9007199254740992", "NaN", "9223372036854775808"]:
		settings.default_text.text = invalid
		settings.stage_variable()
		assert(settings.draft.groups.size() == 2)
	settings.default_text.text = "-12"
	settings.stage_variable()
	choose(settings.variable, "token_name")
	settings._refresh_variable()
	settings.default_text.text = "copper token"
	settings.stage_variable()
	choose(settings.source_chapter, "briefing")
	settings._refresh_jumps()
	choose(settings.source_jump, "10")
	settings._refresh_link()
	assert(settings.current_link.text.contains("followup"))
	choose(settings.target_chapter, "followup")
	settings._fill_nodes(settings.target_node, "followup")
	choose(settings.target_node, "2")
	settings.stage_link()
	assert(settings.draft.groups.size() == 5)
	assert(settings.draft.operations().size() == 6)
	assert(settings.draft.source.bundle.package.registry.characters.rend.name == "Rend")
	# Hide and reopen preserve the unsent batch and original revision.
	settings.hide()
	panel.open_story_settings()
	assert(settings.draft.operations().size() == 6)
	assert(settings.draft.source.revision == 1)
	# A different service URL cannot receive this draft.
	panel.server.text = "http://127.0.0.1:1"
	settings.message.text = "Wrong service must not receive edits"
	await settings.propose()
	assert(settings.status.text.contains("original service"))
	assert(settings.draft.operations().size() == 6)
	panel.server.text = OS.get_environment("ARROW_STORY_URL")
	settings.message.text = ""
	await settings.propose()
	assert(settings.status.text.contains("review message"))
	settings.tabs.current_tab = 3
	await screenshot("story-settings-links")
	settings.tabs.current_tab = 4
	await screenshot("story-settings-batch")
	settings.message.text = "Change shared names, typed defaults and chapter routing"
	await settings.propose()
	assert(panel.reviewed.status == "ready", panel.review.text)
	assert(panel.reviewed.operations.size() == 6)
	assert(settings.draft.groups.is_empty())
	var first_proposal = panel.reviewed.id
	await panel.apply_reviewed()
	assert(panel.status.text.contains("Applied revision 2"), panel.status.text)
	# New edits from the old source remain stale even after refreshing the parent service window.
	settings.character_name.text = "Stale staged name"
	settings.stage_character()
	await panel.load_story()
	assert(panel.snapshot.revision == 2)
	assert(settings.draft.source.revision == 1)
	settings.message.text = "Retain stale edits after a conflict"
	await settings.propose()
	assert(panel.review.text.contains("revision_conflict"))
	assert(settings.draft.groups.size() == 1)
	assert(settings.draft.source.revision == 1)
	settings.pending.select(0)
	settings.remove_selected()
	assert(settings.draft.groups.is_empty())
	panel.open_story_settings()
	assert(settings.draft.source.revision == 2)
	assert(settings.character_name.text == "Rend of the Lantern Guild")
	# Link removal and a local target form one atomic batch.
	choose(settings.source_chapter, "briefing")
	settings._refresh_jumps()
	choose(settings.local_target, "2")
	settings.stage_local_jump()
	assert(settings.draft.operations().size() == 2)
	settings.message.text = "Use a local Jump destination"
	await settings.propose()
	assert(panel.reviewed.status == "ready", panel.review.text)
	await panel.apply_reviewed()
	assert(panel.status.text.contains("Applied revision 3"))
	await panel.load_story()
	panel.open_story_settings()
	choose(settings.source_chapter, "briefing")
	settings._refresh_jumps()
	assert(settings.current_link.text.contains("local node 2"))
	choose(settings.target_chapter, "followup")
	settings._fill_nodes(settings.target_node, "followup")
	choose(settings.target_node, "1")
	settings.stage_link()
	assert(settings.draft.operations()[0].data.target == -1)
	settings.message.text = "Convert a local Jump back to a chapter link"
	await settings.propose()
	assert(panel.reviewed.status == "ready", panel.review.text)
	write_result({"first_proposal": first_proposal, "proposal": panel.reviewed.id})
	print("Arrow service UI: PASS — shared registries, typed defaults, chapter links, local conversion, frozen batches and conflicts")
	quit(0)
