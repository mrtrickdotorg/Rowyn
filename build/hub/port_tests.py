"""Adapt only upstream tests for deliberately removed surfaces.

Keep tests for retained Apps/Updates/VPN/job/IPC behavior; removed upstream
features have no claim in Rowyn. New pages have their own behavioral tests.
Every title/line splice is counted against the exact pin and fails closed.
"""
import re


REMOVED_TESTS = (
    "Guardian storage verification runs only after an explicit completed maintenance result",
    "release summary is tied to the checked image and explains unavailable notes",
    "update history lists past releases with expandable notes",
    "container build provenance has the required GitHub token permissions",
    "image build notes are digest-bound and required before publishing",
    "staging announces download before bootc begins pulling the image",
    "Home Guardian activity exposes expandable current recommendations",
    "phase-2 onboarding is state-driven, not copy",
    "Play setup waits for required state and ignores optional unknowns",
    "phase-3 graphics truth is wired, not implied",
    "phase-4 backend honesty is enforced, not documented",
    "bug-hunt round 3: installer fail-closed and daemon bounds",
    "Gaming Flathub setup runs as a visible phase inside the tracked install job",
    "bug-hunt round 5: untrusted archives stay untrusted",
    "bug-hunt round 3: destructive privileged ops require modal confirmation",
)


def adapt(web, replace_once):
    path = web / "tests/command-contract.test.mjs"
    text = path.read_text()
    for title in REMOVED_TESTS:
        pattern = r'(?m)^test\("' + re.escape(title) + r'",[\s\S]*?(?=^test\(|\Z)'
        text, count = re.subn(pattern, '', text)
        if count != 1:
            raise ValueError(f"expected one upstream test to scope out: {title}")
    # These files were pruned from the shipped source, not replaced with fakes.
    for name in ("gamingRust", "devToolsRust", "sharedGuardianRust", "safeUpgradeRust"):
        text, count = re.subn(r'^const ' + name + r' = await readFile[^\n]+\n', '', text, flags=re.M)
        if count != 1:
            raise ValueError(f"missing pruned test import {name}")
    for line in (
        '  "fetchGuardianSnapshot",\n',
        '  "fetchUpdateReleaseSummary",\n',
        '  "guardian_snapshot",\n',
        '  "run_hub_action",\n',
        '  "hub_action_status",\n',
        '  "hub_action_cancel",\n',
        '  "privileged_action_cancel",\n',
        '  "guardian_check_cancel",\n',
        '  "gaming_job_cancel",\n',
        '    [gamingRust, "gaming_tool_install"],\n',
        '    [gamingRust, "gaming_tool_uninstall"],\n',
        '    [gamingRust, "gaming_tool_launch"],\n',
        '    [rust, "guardian_execute_recipe"],\n',
        '    [rust, "guardian_dismiss"],\n',
        '    [devToolsRust, "dev_tools_install_selection"],\n',
        '  assert.ok(gaming.includes(\'useSectionAction(["hub-action", "gaming"])\'));\n',
        '  assert.ok(repairSection.includes(\'useSectionAction(["update", "install", "hub-action"])\'));\n',
        '  assert.ok(channelsSection.includes(\'useSectionAction("update")\'));\n',
        '    ["Guardian", guardian, ["fetchGuardianSnapshot", "runGuardianCheck", "runGuardianControl"]],\n',
        '    ["Hardware", hardware, ["fetchHardwareSnapshot", "fetchHardwareViewSummary", "fetchLoadedKernelModules"]],\n',
        '    ["Gaming", gaming, ["fetchGamingLibrary", "fetchGamingSliceAvailable", "fetchProtonDbMany", "fetchAntiCheatTable"]],\n',
        '  assert.match(guardian, /controlGuardian/);\n',
    ):
        # Some literal command names occur both in the ledger and cancel test.
        if line.strip().startswith('"'):
            count = text.count(line)
            if count < 1:
                raise ValueError(f"missing scoped command {line}")
            text = text.replace(line, '')
        else:
            text = replace_once(text, line, '')
    # Root-daemon implementation is gone; retain the user-facing mapper tests.
    start = text.index('  assert.match(privilegeRust, /PRIVILEGED_ERROR_TAG:')
    end = text.index('  for (const [mapper, label]', start)
    text = text[:start] + text[end:]
    # Mixed legacy regressions: remove only assertions/reads for absent sibling
    # installer or gaming sources; retain SMB, VPN, exe trust and focus checks.
    text = replace_once(text,
        '  const gaming = await readFile(resolve(root, "src-tauri/src/commands/gaming.rs"), "utf8");\n'
        '  assert.doesNotMatch(gaming, /bash/, "gaming install must not shell out");\n'
        '  assert.match(gaming, /ensure_flathub_user_remote/, "remote-add must precede install");\n', '')
    start = text.index('  const planCommit = await readFile(')
    end = text.index('  assert.match(rust, /atomic_write_text', start)
    text = text[:start] + text[end:]
    # Scan the ENTIRE frontend, including liveData and Apps/Updates/VPN.
    # Only exact removed-surface legacy wrappers are exempt; a new unregistered
    # command anywhere else still fails. The MVP behavioral suite checks its
    # native bridge separately, and the ledger covers dynamic job selectors.
    text = replace_once(text,
        '  for (const command of invoked) {\n    assert.match(handler,',
        '''  const removedLegacy = new Set([
    "ai_dev_box_status", "dev_tools_catalog_list", "dev_tools_install_selection",
    "fix_discord_screenshare", "fix_obs_pipewire", "gaming_perf_status",
    "gaming_tool_install", "gaming_tool_launch", "gaming_tool_uninstall", "gaming_tools",
    "guardian_dismiss", "just_list", "open_cloud_storage_app", "open_game_folder",
    "open_move_files_app", "open_network_shares_app", "per_game_launch_options",
    "per_game_profile", "save_per_game_profile", "scx_available", "scx_set_scheduler", "scx_status",
  ]);
  for (const command of invoked) {
    if (removedLegacy.has(command)) continue;
    assert.match(handler,''')
    # New bridge uses single-quoted literals too.
    text = replace_once(text, r'/invoke(?:<[^>]+>)?\(\"([^\"]+)\"/g', r'''/invoke(?:<[^>]+>)?\(["']([^"']+)["']/g''')
    native = web / "src-tauri/src/commands/rowyn_steam.rs"
    commands = (web / "src-tauri/src/commands/mod.rs").read_text()
    main = (web / "src-tauri/src/main.rs").read_text()
    if not native.is_file() or "pub(crate) mod rowyn_steam;" not in commands:
        raise ValueError("Steam metadata scanner overlay was not exported")
    if "commands::rowyn_steam::rowyn_steam_games_scan" not in main:
        raise ValueError("Steam metadata scan command is not registered")
    # Removal confirmation now belongs to the rendered App Store flow, not
    # a native window.confirm prompt in the low-level shared IPC wrapper.
    text = replace_once(text, r'  assert.match(service, /Uninstall \$\{id\}\?/);',
        r'''  assert.match(apps, /AppRemovalConfirmation/);
  assert.match(apps, /setRemoval\(app\)/);
  assert.match(apps, /if \(!removal \|\| busy !== null\) return/);''')
    # Rowyn names the staged-update restart for what it does.
    text = replace_once(text, 'assert.match(updatesOverview, /Choose “Restart to apply”/);',
                        'assert.match(updatesOverview, /Choose “Restart to finish updating”/);')
    path.write_text(text)

    path = web / "tests/hub-shell-smoke.test.mjs"
    text = path.read_text()
    for line in ('      "Play.tsx": sectionsModule.PLAY_SECTIONS,\n',
                 '      "ThisPc.tsx": sectionsModule.THIS_PC_SECTIONS,\n',
                 '      "MoveIn.tsx": sectionsModule.MOVE_IN_SECTIONS,\n'):
        text = replace_once(text, line, '')
    text = replace_once(text, '      "Apps.tsx": sectionsModule.APPS_SECTIONS,',
        '      "Apps.tsx": sectionsModule.APPS_SECTIONS,\n      "Vpn.tsx": sectionsModule.VPN_SECTIONS,')
    # Readiness handoffs use router context on Diagnostics as well as Home.
    # React Router 7 exports StaticRouter directly; use its actual SSR context.
    text = replace_once(text,
        '    const { MemoryRouter } = await server.ssrLoadModule("react-router-dom");',
        '    const { StaticRouter } = await server.ssrLoadModule("react-router-dom");')
    text = replace_once(text,
        'renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(dashboardMod.Dashboard)))',
        'renderToStaticMarkup(React.createElement(StaticRouter, { location: "/" }, React.createElement(dashboardMod.Dashboard)))')
    text = replace_once(text, '    assert.equal(failures.length, 0,', '''    for (const [page, name] of [["RowynDiagnostics.tsx", "Diagnostics"], ["RowynPlay.tsx", "Play"], ["RowynDevelopment.tsx", "Development"], ["Backups.tsx", "Backups"], ["RowynWork.tsx", "Work"]]) {
      const mod = await server.ssrLoadModule(`/src/pages/${page}`);
      const markup = renderToStaticMarkup(React.createElement(StaticRouter, { location: "/" }, React.createElement(mod[name])));
      assert.match(markup, /Rowyn/);
      rendered += 1;
    }
    assert.equal(failures.length, 0,''')
    text = replace_once(text, 'assert.ok(rendered >= 21,', 'assert.equal(rendered, 9,')
    path.write_text(text)
