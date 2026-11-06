# NetBlade

NetBlade is a desktop network toolkit for Windows, macOS and Linux. It shows if a host is reachable, where the path to it loses packets or slows down, what DNS returns, how a web server answers, and how your network adapters are set up. No command line is necessary.

NetBlade has four tools:

- **Ping and trace:** a continuous traceroute with loss and latency for each hop.
- **DNS lookup:** queries for one record type, from your system nameserver or a nameserver you choose.
- **IP config:** your adapter configuration, the same data as `ipconfig /all`.
- **curl:** an HTTP request built from a form. It shows the curl command, the response and the time of each phase.

NetBlade does not need administrator rights.

## Download

1. Go to the [Releases page](https://github.com/TariqMK/NetBlade/releases).
2. Download the file for your computer:
   - Windows (x64): `NetBlade-vX.Y.Z-windows-x64.exe`
   - macOS (Apple Silicon): `NetBlade-vX.Y.Z-macos-arm64.zip`
   - Linux (x64): `NetBlade-vX.Y.Z-linux-x64.tar.gz`
3. Windows: run the `.exe` file. No installation is necessary.
4. macOS: open the `.zip` file. Move `NetBlade.app` to the Applications folder.
5. Linux: extract the `.tar.gz` file. Run the `NetBlade` file.

**Note:** The builds are not code-signed. Windows SmartScreen can show a warning. Select **More info**, then **Run anyway**.

**Note:** On macOS, the first time you open NetBlade, macOS blocks it. To allow it:

1. Open **System Settings** > **Privacy & Security**.
2. Scroll down to the message about NetBlade.
3. Select **Open Anyway**.

## Use

Select a tool in the sidebar. Or press **Ctrl+1** for Ping and trace, **Ctrl+2** for DNS lookup, **Ctrl+3** for IP config and **Ctrl+4** for curl. The version shows next to the app name at the top of the sidebar.

NetBlade follows the light or dark appearance of your system.

### Ping and trace

1. Type a host name or IPv4 address in the target field, for example `1.1.1.1`.
2. Select the probe interval: **Every 0.5 s**, **Every 1 s**, **Every 2 s** or **Every 5 s**. You can change it while the trace runs.
3. Select **Start**, or press Enter.
4. Read the results:
   - The summary line shows the IP address, the number of hops, the loss at the destination and the average latency.
   - The chart shows the latency of the last 60 probes, in ms. A red tick on the baseline is a lost probe.
   - The hop table shows one row for each router on the path. The columns are **Loss**, **Sent**, **Last**, **Avg**, **Best** and **Worst**. Latency is in ms.
   - The dot on the route line shows the loss at that hop. Green is 0%. Amber is less than 20%. Red is 20% or more. Grey is no reply.
   - The **Range** bar shows the best-to-worst latency. The tick shows the average. Hover over the bar to see the values.
5. To chart one hop, select its row in the hop table.
6. Select **Stop** to end the trace.

**Note:** Many routers do not reply to traceroute probes, or reply slowly. A hop with **No reply** or with loss is usually not a problem when the hops after it show 0% loss.

**Note:** Ping and trace uses IPv4 only. A host name resolves to its first IPv4 address.

### DNS lookup

1. Type a name in the name field, for example `example.com`.
2. Select the record type: A, AAAA, CNAME, MX, NS, TXT, SOA, SRV, PTR or CAA.
3. Select the nameserver:
   - **System default** uses the nameserver of your computer.
   - **1.1.1.1 (Cloudflare)**, **8.8.8.8 (Google)** or **9.9.9.9 (Quad9)** use that public nameserver.
   - To use a different nameserver, type its IPv4 or IPv6 address in the field.
4. Select **Look up**, or press Enter.
5. Read the results. The summary line shows the number of records, the nameserver that answered and the response time. The table shows **Name**, **Type**, **TTL** and **Value** for each record.

**Note:** For a PTR lookup, type the IP address. NetBlade makes the reverse name for you.

MX records show in preference order. SRV records show in priority order. To copy records, select the rows and press **Ctrl+C**.

### IP config

The tool reads the configuration when you open it.

1. Select **Adapters** to see one card for each connected adapter. A card shows the IPv4 address, subnet mask, default gateway, DNS servers, DHCP server, MAC address, IPv6 addresses, DNS suffix and DHCP state.
2. To also see disconnected adapters, select **Show disconnected adapters**.
3. Select **Raw output** to see the full command output.
4. Select **Copy** to copy the raw output.
5. Select **Refresh** to read the configuration again.

On Windows, IP config runs `ipconfig /all`. On macOS, it runs `ifconfig`, `route -n get default` and `scutil --dns`. On Linux, it runs `ip addr`, `ip route` and `resolvectl status`. On macOS and Linux, only **Raw output** is available.

**Note:** The **Adapters** view needs Windows in English. In other languages, NetBlade shows **Raw output**.

### curl

The curl tool runs the curl program of your system with the options you set. The command box always shows the exact command. You can copy it and run it in a terminal.

1. Select the method: GET, HEAD, POST, PUT, PATCH, DELETE or OPTIONS.
2. Type the URL, for example `https://example.com`.
3. Set the options you need:
   - **Follow redirects:** follows 3xx redirects (`-L`). This is on by default.
   - **Ignore TLS errors:** accepts self-signed or expired certificates (`-k`).
   - **Any IP version**, **IPv4 only** or **IPv6 only** (`-4`, `-6`).
   - **Any HTTP version**, **HTTP/1.0**, **HTTP/1.1** or **HTTP/2**.
   - **Connect to IP:** connects to this IP address but keeps the host name of the URL (`--resolve`). Use it to test one server behind a load balancer or a CDN.
   - **Timeout:** the maximum time for the request, 1 to 600 s (`--max-time`). The default is 30 s.
4. To send headers or a body, select **Headers and body**. Type one header on each line, for example `Accept: application/json`. Type the body in the right box.
5. To use any other curl option, type it in **More options**. Or select **Suggestions** and select a common option, then change its values.
6. Select **Send**, or press Enter. To stop a request, select **Cancel**.
7. Read the results:
   - The status line shows the status code, the HTTP version, the server IP address and port, the size and the redirects. Green is 2xx. Amber is 4xx. Red is 5xx or a failure.
   - The timing bar shows the time for DNS, Connect, TLS, Server and Transfer, in ms. The slowest phase shows in the accent colour.
   - **Headers** shows the request and response headers. A blank line separates each redirect.
   - **Body** shows the response body. JSON is formatted. Binary content is not shown.
   - **Verbose** shows the full `curl -v` output.
8. Select **Copy** to copy the view. Select **Copy command** to copy the command.

**Note:** Server is the time from the sent request to the first byte of the response. A long Server time usually means a slow application, not a slow network.

**Note:** With redirects, each phase is the total over all requests in the chain.

**Note:** On Windows, the command starts with `curl.exe`. In Windows PowerShell, `curl` runs Invoke-WebRequest, not curl.

## Known limitations

- Ping and trace does not trace to IPv6 addresses.
- On Linux, Ping and trace needs unprivileged ICMP. Some distributions block it. If the tool shows an ICMP error, ask your administrator to allow your group in `net.ipv4.ping_group_range`.
- Ping and trace on macOS and Linux is not yet tested on real systems.
- The curl tool needs curl on the system. Windows 10 and later and macOS include it. On Linux, install the `curl` package if it is missing.
- The curl tool shows the first 1 MB of a body.
- The copied curl command uses double quotes on Windows. In PowerShell, a value that contains `"` or `$` can need changes.

## Run From Source

Requires Python 3.12 or later.

1. Clone the repository.
2. Create a virtual environment and install NetBlade with the development tools:

   ```bash
   python -m venv .venv
   ```

   ```bash
   .venv/Scripts/pip install -e ".[dev]"
   ```

   On macOS and Linux, use `.venv/bin/pip`.
3. Start the app:

   ```bash
   python -m netblade
   ```

A source run shows the development version `v0.0.0.dev0`.

## Run Tests

```bash
pytest
```

The tests send no network traffic. The traceroute tests use a simulated path. The curl tests run curl on a local file and on a closed local port.

Check code style with:

```bash
ruff check .
```

## Creating a Release

Releases are made by the **Release** workflow in GitHub Actions. The workflow does not commit or push to any branch. It creates only a tag and a GitHub release.

1. On GitHub, open the **Actions** tab.
2. Select **Release**.
3. Select **Run workflow**. Keep the branch set to `main`.
4. Set **Draft or public release**:
   - `draft`: creates a draft release. Only you can see it. Publish it later on the Releases page.
   - `public`: creates a published release.
5. Set **Version bump**:
   - `patch`: v1.2.3 to v1.2.4. For fixes.
   - `minor`: v1.2.3 to v1.3.0. For new features.
   - `major`: v1.2.3 to v2.0.0. For large changes.
6. Select **Run workflow**.

The workflow does these steps:

1. Finds the highest existing version in the git tags and GitHub releases, including drafts, then applies the bump.
2. Runs the lint check and the tests. If they fail, nothing is built.
3. Builds NetBlade for Windows x64, macOS Apple Silicon and Linux x64. The version is applied on the build machine only.
4. Starts each build without a display. The check fails if the build does not run, shows the wrong version, or is missing the DNS record modules or the Qt SVG image plugin.
5. Tags the built commit and creates the release, with the three files attached and generated release notes.

**Note:** No secrets are necessary. The workflow uses the built-in GitHub token.
