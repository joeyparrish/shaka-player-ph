# Shaka Player Project Health (PH)

Project Health (PH) metrics for Shaka Player

See https://joeyparrish.github.io/shaka-player-ph/


## How does it work?

The dashboard uses a modified version of [freeboard][].  It also uses a
modified version of the [freeboard-jqplot][] plugin to show graphs.
Finally, the custom [freeboard-ph][] plugin displays Project Health (PH)
metrics as defined by Google.

The raw data that drives all this is collected by Python scripts that live in
the `ph/` folder.  They call the GitHub API through the `gh` command-line tool,
then process the data into JSON files that are consumed by freeboard.

A GitHub Actions workflow updates the metrics and deploys everything to GitHub
Pages every morning.


## Token scope

The token used by the workflow requires `repo` scope to download workflow
artifacts.


[freeboard]: https://github.com/Freeboard/freeboard
[freeboard-jqplot]: https://github.com/jritsema/freeboard-jqplot
[freeboard-ph]: https://github.com/joeyparrish/shaka-player-ph/blob/main/freeboard-ph/ph.js
