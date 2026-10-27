extends SceneTree

func _initialize() -> void:
	call_deferred("run_tests")

func run_tests() -> void:
	var main = load("res://main.tscn").instantiate()
	root.add_child(main)
	await process_frame
	for source in ["res://examples/dialogic_bridge/guild_briefing.arrow", "res://examples/dialogic_bridge/story/briefing.arrow", "res://examples/dialogic_bridge/story/followup.arrow"]:
		var project = main.Mind.ProMan.read_project_file_data(source, true)
		assert(project != null, "Arrow rejected " + source)
		main._AUTO_NODE_UPDATE = false
		main.Mind.load_project(project)
		for scene in project.resources.scenes:
			main.Mind.load_scene(scene)
			for i in 8:
				await process_frame
			assert(main.Mind.get_current_open_scene_id() == scene)
			assert(main.Grid._DRAWN_NODES_BY_ID.size() == project.resources.scenes[scene].map.size())
			for id in project.resources.scenes[scene].map:
				main.Mind.inspect_node(id, scene, true)
				for i in 2:
					await process_frame
			if DisplayServer.get_name() != "headless":
				main.UI.set_panel_visibility("inspector", false)
				main.Grid.set_grid_view(Vector2(-40, -40), 0.5)
				await create_timer(0.2).timeout
				RenderingServer.force_draw()
				root.get_texture().get_image().save_png("res://arrow-%s-scene-%s.png" % [source.get_file().get_basename(), scene])
	print("Arrow example: single-document and linked chapter examples opened, all graph nodes drawn and inspectors loaded")
	quit(0)
