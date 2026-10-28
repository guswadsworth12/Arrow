extends SceneTree
const Client = preload("res://scripts/editor/story_service_client.gd")
var main
var panel

func _initialize() -> void:
	create_timer(60).timeout.connect(func(): push_error("Arrow service UI test timed out"); quit(1))
	call_deferred("run_tests")

func select_story(story: String) -> void:
	for index in panel.stories.item_count:
		if panel.stories.get_item_metadata(index) == story:
			panel.stories.select(index)
			return
	assert(false, "Missing fixture story")

func select_chapter(chapter: String) -> void:
	for index in panel.chapters.item_count:
		if panel.chapters.get_item_text(index) == chapter:
			panel.chapters.select(index)
			return
	assert(false, "Missing chapter")

func write_result(value: Dictionary) -> void:
	var file = FileAccess.open(OS.get_environment("ARROW_TEST_OUTPUT"), FileAccess.WRITE)
	file.store_string(JSON.stringify(value))
	file.close()

func edit_chapter(content: String) -> void:
	main.Mind.update_resource(2, {"data": {"content": content}}, "nodes")
	main.Mind.reset_project_save_status(false)

func run_tests() -> void:
	assert(Client.valid_url("http://127.0.0.1:8765"))
	assert(Client.valid_url("http://[::1]:8765"))
	assert(Client.valid_url("https://example.com/story"))
	for url in ["http://remote.example", "http://localhost.evil", "https://user:pass@example.com", "https://example.com?token=x", "https://example.com\n", "file:///tmp/foo"]:
		assert(not Client.valid_url(url), "Unsafe URL accepted")
	assert(not Client.read_bundle("res://examples/dialogic_bridge/story/briefing.arrow").ok)
	var bundle = Client.read_bundle("res://examples/dialogic_bridge/story/guild_story.json")
	assert(bundle.ok)
	main = load("res://main.tscn").instantiate()
	root.add_child(main)
	await process_frame
	main.open_story_service()
	panel = main.StoryService
	panel.server.text = OS.get_environment("ARROW_STORY_URL")
	panel.token.text = OS.get_environment("ARROW_STORY_API_TOKEN")
	assert(panel.token.secret)
	await panel.connect_service()
	var story = OS.get_environment("ARROW_TEST_STORY")
	if OS.get_environment("ARROW_TEST_MODE") == "import":
		await panel.import_manifest(OS.get_environment("ARROW_TEST_MANIFEST"))
		assert(panel.status.text.contains("review message"))
		panel.message.text = "Import through Arrow UI"
		await panel.import_manifest(OS.get_environment("ARROW_TEST_MANIFEST"))
		assert(panel.snapshot.story_id == story, panel.status.text)
		assert(panel.snapshot.revision == 1)
		select_chapter("briefing")
		panel.open_chapter()
		await process_frame
		edit_chapter("UI imported and edited this chapter.")
		await panel.propose_current()
		assert(panel.reviewed.status == "ready", panel.review.text)
		write_result({"base_revision": panel.reviewed.base_revision})
		print("Arrow service UI: PASS — native manifest import and editor proposal")
		quit(0)
		return
	select_story(story)
	if OS.get_environment("ARROW_TEST_MODE") == "restart":
		var binding_id = ""
		for id in panel.bindings:
			if panel.bindings[id].story_id == story and panel.bindings[id].revision == 2:
				binding_id = id
		assert(not binding_id.is_empty(), "Binding did not survive restart")
		main.Mind.open_project(int(binding_id))
		await process_frame
		edit_chapter("Another edit after restart.")
		panel.message.text = "Restart retains original base"
		await panel.propose_current()
		assert(panel.reviewed.status == "ready", panel.status.text)
		assert(panel.reviewed.base_revision == 2)
		write_result({"base_revision": panel.reviewed.base_revision})
	else:
		await panel.load_story()
		assert(panel.snapshot.revision == 1)
		select_chapter("briefing")
		panel.open_chapter()
		await process_frame
		var first_uid = main.Mind.ProMan.get_active_project_id()
		assert(first_uid >= 0, panel.status.text)
		assert(panel.bindings[str(first_uid)].revision == 1)
		assert(main.Mind._PROJECT.resources.nodes.has(2))
		for i in 8:
			await process_frame
		assert(main.Grid._DRAWN_NODES_BY_ID.has(2))
		panel.popup_centered()
		await panel.refresh_proposals()
		for index in panel.proposals.item_count:
			if panel.proposals.get_item_metadata(index) == OS.get_environment("ARROW_TEST_PROPOSAL"):
				panel.proposals.select(index)
		await panel.review_selected()
		assert(panel.reviewed.status == "ready", panel.status.text)
		assert(panel.review.text.contains("A request revised through MCP."))
		assert(panel.review.text.contains("Godot: 4."))
		assert(not panel.apply_button.disabled)
		if DisplayServer.get_name() != "headless":
			await create_timer(0.3).timeout
			RenderingServer.force_draw()
			panel.get_texture().get_image().save_png(OS.get_environment("ARROW_TEST_OUTPUT").get_base_dir().path_join("story-service-review.png"))
		await panel.apply_reviewed()
		assert(panel.status.text.contains("Applied revision 2"), panel.status.text)
		assert(panel.apply_button.disabled)
		edit_chapter("This edit has a stale base.")
		panel.message.text = "Do not overwrite another writer"
		await panel.load_story()
		assert(panel.snapshot.revision == 2)
		assert(panel.bindings[str(first_uid)].revision == 1, "Refresh rebased an old local chapter")
		await panel.propose_current()
		assert(panel.review.text.contains("revision_conflict"), panel.review.text)
		assert(panel.reviewed.is_empty())
		# Unsaved changes prevent service chapter open and are preserved.
		panel.open_chapter()
		assert(main.Mind.ProMan.get_active_project_id() == first_uid)
		main.Mind.save_project()
		select_chapter("briefing")
		panel.open_chapter()
		await process_frame
		assert(main.Mind.ProMan.get_active_project_id() != first_uid)
		edit_chapter("[signal=invalid]")
		panel.message.text = "Invalid editor content is retained as diagnostics"
		await panel.propose_current()
		assert(panel.reviewed.status == "invalid", panel.review.text)
		assert(panel.apply_button.disabled)
		edit_chapter("Changed inside Arrow.")
		panel.message.text = "Arrow visual chapter edit"
		await panel.propose_current()
		assert(panel.reviewed.status == "ready", panel.review.text)
		assert(panel.reviewed.base_revision == 2)
		write_result({"proposal": panel.reviewed.id})
		main.Mind.save_project()
	print("Arrow service UI: PASS — shared MCP review/apply, graph edit, conflict, revision binding and credential isolation")
	quit(0)
