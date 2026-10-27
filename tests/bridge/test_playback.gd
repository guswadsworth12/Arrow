extends SceneTree

var handler: Node
var seen: Array[String] = []
var ended: bool = false

func _initialize() -> void:
	call_deferred("run_tests")

func run_tests() -> void:
	handler = root.get_node("Dialogic")
	handler.Text.text_finished.connect(func(info): seen.append(info.text))
	handler.timeline_ended.connect(func(): ended = true)
	var directory = DialogicResourceUtil.get_timeline_directory()
	var timeline_path = directory.get("arrow_story_" + "res://example.arrow".sha256_text().left(8), "")
	assert(not timeline_path.is_empty(), "Importer did not register the timeline")
	for option in [2, 1, 2]:
		# The game deliberately resets state for the first two runs only. A third
		# run asks again after acceptance to prove start() preserves game state.
		if option == 1 or seen.is_empty():
			handler.VAR.var_storage = {"request_accepted": false}
		seen.clear()
		ended = false
		handler.start(timeline_path)
		var chosen = false
		var branch_captured = false
		var deadline = Time.get_ticks_msec() + 20000
		while not ended and Time.get_ticks_msec() < deadline:
			await create_timer(0.1).timeout
			if handler.current_state == handler.States.REVEALING_TEXT:
				handler.Text.skip_text_reveal()
			elif handler.current_state == handler.States.AWAITING_CHOICE and not chosen:
				if not handler.Choices._choice_blocker.is_stopped():
					continue
				var button = handler.Choices.get_choice_button(option)
				assert(button != null and button.is_visible_in_tree(), "Missing rendered choice button")
				assert("Accept" in handler.Choices.get_choice_button(1).text)
				assert("Ask" in handler.Choices.get_choice_button(2).text)
				await capture("choices-%s.png" % option)
				handler.Choices.select_choice(option)
				chosen = true
			elif handler.current_state == handler.States.IDLE and not seen.is_empty():
				if not branch_captured and (seen[-1].contains("journal") or seen[-1].contains("board.")) and chosen:
					await capture("branch-%s-%s.png" % [option, handler.VAR.get_variable("request_accepted")])
					branch_captured = true
				handler.Inputs.dialogic_action.emit()
		assert(ended and chosen and branch_captured, "Playback stalled or did not reach a branch outcome")
		var accepted = handler.VAR.get_variable("request_accepted")
		assert(seen.has("The request is added to your journal.") if accepted else seen.has("The request remains on the board."))
		assert(not seen.has("The request remains on the board.") if accepted else not seen.has("The request is added to your journal."))
		assert(seen[-1] == "Outside, Valenne carries on around you.", "Cross-scene jump did not reach the follow-up")
		assert(not seen.has("Who posted this request?") if option == 1 else seen.has("Who posted this request?"))
		print("Dialogic Alpha 20 playback: choice %s, accepted=%s, correct branch and follow-up reached" % [option, accepted])
	print("Dialogic Alpha 20 playback: both rendered choices and branches, cross-scene jump and persistent state verified")
	quit(0)

func capture(filename: String) -> void:
	if DisplayServer.get_name() != "headless":
		RenderingServer.force_draw()
		root.get_texture().get_image().save_png("res://" + filename)
