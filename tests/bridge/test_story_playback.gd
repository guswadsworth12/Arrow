extends SceneTree
var handler: Node
var seen: Array[String] = []
var speakers: Array[String] = []
var ended: bool = false

func _initialize() -> void:
	call_deferred("run_tests")

func run_tests() -> void:
	handler = root.get_node("Dialogic")
	handler.Text.text_finished.connect(func(info):
		seen.append(info.text)
		if info.character:
			var character = DialogicResourceUtil.get_character_resource(info.character)
			assert(character != null, "Runtime speaker is not a registered character")
			speakers.append(character.resource_path))
	handler.timeline_ended.connect(func(): ended = true)
	var source = "res://story/guild_story.json"
	var timeline = DialogicResourceUtil.get_timeline_directory().get("arrow_story_" + source.sha256_text().left(8), "")
	assert(not timeline.is_empty())
	# Ask, accept, ask with persisted state, and enter chapter two directly.
	for option in [2, 1, 2, 0]:
		if option == 1 or seen.is_empty():
			handler.VAR.var_storage = {"request_accepted": false}
		seen.clear()
		speakers.clear()
		ended = false
		handler.start(timeline, "arrow_followup__1" if option == 0 else "")
		var chosen = option == 0
		var captured = false
		var deadline = Time.get_ticks_msec() + 20000
		while not ended and Time.get_ticks_msec() < deadline:
			await create_timer(0.1).timeout
			if handler.current_state == handler.States.REVEALING_TEXT:
				handler.Text.skip_text_reveal()
			elif handler.current_state == handler.States.AWAITING_CHOICE and not chosen:
				if not handler.Choices._choice_blocker.is_stopped():
					continue
				assert(handler.Choices.get_choice_button(option) != null)
				handler.Choices.select_choice(option)
				chosen = true
			elif handler.current_state == handler.States.IDLE and not seen.is_empty():
				if not captured and "this chapter." in seen[-1]:
					if DisplayServer.get_name() != "headless":
						RenderingServer.force_draw()
						root.get_texture().get_image().save_png("res://story-branch-%s-%s.png" % [option, handler.VAR.get_variable("request_accepted")])
					captured = true
				handler.Inputs.dialogic_action.emit()
		assert(ended and chosen and captured, "Story did not reach a later-chapter outcome")
		var accepted = handler.VAR.get_variable("request_accepted")
		assert(seen.has("Your accepted request carries into this chapter.") if accepted else seen.has("The request is still undecided in this chapter."))
		assert(not seen.has("The request is still undecided in this chapter.") if accepted else not seen.has("Your accepted request carries into this chapter."))
		assert(seen[-1] == "Outside, Valenne carries on around you.")
		assert(seen.has("A new chapter begins."))
		assert(speakers.size() == (1 if option in [0, 1] else 2))
		assert(speakers.all(func(speaker): return speaker == speakers[0]), "Chapters did not resolve the same character")
		print("Story package playback: choice=%s, accepted=%s, shared character and later-chapter state verified" % [option, accepted])
	print("Story package playback: both linked outcomes, persistent shared state and direct chapter entry verified")
	quit(0)
