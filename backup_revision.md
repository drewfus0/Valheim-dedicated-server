Backup improvements.
can we only backup the world's folder that the current active world is located in? (so world or the world_local)not both.
Add the logs
Add the web manager configs and data(eg the .json files.)
Add server config files that are outside the worlds folder

Restore improvements.
We should be able to restore the world file, any of the .json files we have, server log files, web manager config files, server config files.
Restore should shutdown the server cleanly before restoring. 
It should restore the files to their original locations. 
Options on what is to be restored: logs, web manager configs and data, server configs and logs, world files.
should we clear out the current files before restoring? (the current logs, world files) as these are folders full of changing files that can create new files that are not in the backup as they were created since.