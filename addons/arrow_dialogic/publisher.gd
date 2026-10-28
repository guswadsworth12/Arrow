@tool
extends RefCounted
## Publish one import as a recoverable file set. This handles ordinary I/O errors;
## it is not a filesystem transaction across a power loss or concurrent editors.

var error: String = ""

func publish(files: Dictionary) -> bool:
	error = ""
	var token = str(OS.get_process_id()) + "_" + str(Time.get_ticks_usec())
	var staged: Dictionary = {}
	var backups: Dictionary = {}
	var published: Array[String] = []
	for path in files:
		if DirAccess.dir_exists_absolute(path):
			error = "Output path is a directory: " + path
			_cleanup(staged.values())
			return false
		if DirAccess.make_dir_recursive_absolute(path.get_base_dir()) != OK:
			error = "Cannot create output directory: " + path.get_base_dir()
			_cleanup(staged.values())
			return false
		var temporary: String = path + ".arrow-stage-" + token
		staged[path] = temporary
		var file = FileAccess.open(temporary, FileAccess.WRITE)
		if file == null:
			error = "Cannot stage " + path
			_cleanup(staged.values())
			return false
		file.store_string(files[path])
		file.flush()
		var status = file.get_error()
		file.close()
		if status != OK:
			error = "Cannot finish writing " + path
			_cleanup(staged.values())
			return false
	for path in files:
		if FileAccess.file_exists(path):
			var backup: String = path + ".arrow-backup-" + token
			if _rename(path, backup) != OK:
				error = "Cannot back up " + path
				_rollback(published, backups, staged)
				return false
			backups[path] = backup
		if _rename(staged[path], path) != OK:
			error = "Cannot replace " + path
			_rollback(published, backups, staged)
			return false
		published.append(path)
	_cleanup(backups.values())
	return true

func _rename(source: String, target: String) -> Error:
	return DirAccess.rename_absolute(source, target)

func _rollback(published: Array[String], backups: Dictionary, staged: Dictionary) -> void:
	for path in published:
		if DirAccess.remove_absolute(path) != OK:
			error += "\nCould not remove new output: " + path
	for path in backups:
		if DirAccess.rename_absolute(backups[path], path) != OK:
			error += "\nRecovery required; preserved previous output at: " + backups[path]
	_cleanup(staged.values())

func _cleanup(paths: Array) -> void:
	for path in paths:
		if FileAccess.file_exists(path) and DirAccess.remove_absolute(path) != OK:
			error += "\nCould not remove temporary file: " + path
