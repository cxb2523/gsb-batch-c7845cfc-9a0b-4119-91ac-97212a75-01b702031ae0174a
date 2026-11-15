#!/usr/bin/env python3

from . import array

import os

__ENCODING = "ISO-8859-1"

def is_file(file: str):
	"""
	Returns 'True' if the 'file' exists and is a regular file.
	"""
	return os.path.isfile(file)

def validate(file: str):
	"""
	Validate a file.\n
	Success flag is 'True' if the file has a read permission and is not empty.
	"""
	success = False
	message = ""
	if not os.access(file, os.R_OK):
		message = f"\"{file}\" does not have a read permission"
	elif not os.stat(file).st_size > 0:
		message = f"\"{file}\" is empty"
	else:
		success = True
	return success, message

def read_array(file: str):
	"""
	Read a file line by line, and append the lines to a list.\n
	Whitespace will be stripped from each line, and empty lines will be removed.\n
	Returns a unique list.
	"""
	tmp = []
	with open(file, "r", encoding = __ENCODING) as stream:
		for line in stream:
			line = line.strip()
			if line:
				tmp.append(line)
	return array.unique(tmp)

def confirm_overwrite(out: str):
	"""
	Confirm overwriting an existing output file.\n
	Returns 'True' if the file does not exist or the overwrite was confirmed.\n
	Without an interactive input the overwrite is confirmed, so that non-interactive runs still save their results.
	"""
	if not os.path.isfile(out):
		return True
	print(f"'{out}' already exists")
	try:
		return input("Overwrite the output file (yes): ").strip().lower() in ["yes", "y", ""]
	except EOFError:
		return True
	except KeyboardInterrupt:
		# the user interrupted the prompt, keep the existing file intact
		print("")
		return False

def overwrite(text: str, out: str, verbose: bool = True):
	"""
	Write a text to an output file.\n
	The text is written to a temporary file first, so an interrupted write never leaves a truncated output file.\n
	With 'verbose' set to 'False', a failed write is reported only through the return value.
	"""
	tmp = f"{out}.tmp"
	try:
		with open(tmp, "w") as stream:
			stream.write(text)
		os.replace(tmp, out)
		if verbose:
			print(f"Results have been saved to '{out}'")
		return True
	except OSError as ex:
		try:
			os.remove(tmp)
		except OSError:
			pass
		if verbose:
			print(f"Cannot save the results to '{out}': {ex}")
		return False
