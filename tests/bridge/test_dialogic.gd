extends SceneTree

func _initialize() -> void:
	call_deferred("run_tests")

func run_tests() -> void:
	# Match the importer: use an existing character resource, not a runtime fallback.
	DialogicResourceUtil.set_directory("dch", {"Rend": "res://rend.tres"})
	var timeline = DialogicTimeline.new()
	timeline.from_text(FileAccess.get_file_as_string("res://compiled.dtl"))
	timeline.process()
	var labels: Dictionary = {}
	var jumps: Array = []
	var choices = 0
	var conditions = 0
	var texts = 0
	var assignments = 0
	var endings = 0
	for event in timeline.events:
		if event is DialogicLabelEvent:
			labels[event.name] = true
		elif event is DialogicJumpEvent:
			jumps.append(event.label_name)
		elif event is DialogicChoiceEvent:
			choices += 1
		elif event is DialogicConditionEvent:
			conditions += 1
		elif event is DialogicTextEvent:
			texts += 1
		elif event is DialogicEndTimelineEvent:
			endings += 1
		elif event is DialogicVariableEvent:
			assignments += 1
	for target in jumps:
		if not labels.has(target):
			printerr("Unresolved Dialogic label: " + target)
			quit(1)
			return
	if labels.size() != 12 or choices != 2 or conditions != 2 or texts != 6 or assignments != 1 or endings != 1:
		printerr("Unexpected parsed event counts: ", [labels.size(), choices, conditions, texts, assignments])
		quit(1)
		return
	# Exercise the parsed assignment and condition against Dialogic's real subsystems.
	var handler = root.get_node("Dialogic")
	handler.VAR.var_storage = {"request_accepted": false}
	var assignment: DialogicVariableEvent
	var condition: DialogicConditionEvent
	for event in timeline.events:
		if event is DialogicVariableEvent:
			assignment = event
		if event is DialogicConditionEvent and event.condition_type == DialogicConditionEvent.ConditionTypes.IF:
			condition = event
	if handler.Expressions.execute_condition(condition.condition):
		printerr("False branch incorrectly evaluates as true")
		quit(1)
		return
	assignment.execute(handler)
	await assignment.event_finished
	if handler.VAR.get_variable("request_accepted") != true or not handler.Expressions.execute_condition(condition.condition):
		printerr("Assignment did not unlock the true branch")
		quit(1)
		return
	timeline.process()
	if handler.VAR.get_variable("request_accepted") != true:
		printerr("Reprocessing reset persistent state")
		quit(1)
		return
	print("Dialogic Alpha 20 parser: event classes, jump targets, variable assignment and both condition outcomes verified")
	timeline = null
	await process_frame
	await process_frame
	quit(0)
