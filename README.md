# kopi

Version: 0.1.0

Create and manage GitHub Projects (v2) from the command line. Wraps the `gh` CLI, so it needs no dependencies beyond Python 3.9+ and an authenticated `gh` with the `project` scope (`gh auth refresh -s project`).

## Usage

```
./kopi.py list
./kopi.py create "Title" --repo owner/repo
./kopi.py view N
./kopi.py delete N
./kopi.py fields N
./kopi.py items N
./kopi.py add N --url <issue-or-pr-url>    # or --title "draft item"
./kopi.py --version
```

Use `--owner` to target an organisation or user.

## Help Support Development

If you find this software useful and would like to support its development, please consider buying me a coffee:

https://ko-fi.com/richardatlateralblast

## License

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). See [LICENSE.md](LICENSE.md).

See [CHANGELOG.md](CHANGELOG.md) for release history.
