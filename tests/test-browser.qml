import QtQuick
import Quickshell

ShellRoot {
  id: root

  property int stage: 0
  property bool finished: false

  function fail(message) {
    console.error("TEST FAILURE:", message)
    finished = true
    Qt.exit(1)
  }

  QtObject {
    id: fakeService

    property string jobsFile: "/nonexistent/jobs.json"
    property string browsePath: Quickshell.env("RESTIC_PLUGIN_DIR") + "/tests/fake-browse.py"
    property string home: "/home/test"
    property string restoreState: ""
    property string restoreName: ""
    property real restorePercent: -1
    property string restoreError: ""
    property var restoreCalls: []

    function restoreEntry(jobId, snapshot, entry) {
      restoreCalls = restoreCalls.concat([[snapshot.shortId, entry.path, entry.type]])
      return "started"
    }
    function clearRestore() {}
  }

  Loader {
    id: loader
    width: 420
    source: "file://" + Quickshell.env("RESTIC_PLUGIN_DIR") + "/SnapshotBrowser.qml"
    onLoaded: {
      item.service = fakeService
      item.open({ id: "home", name: "Home" })
    }
  }

  Timer {
    interval: 50
    running: true
    repeat: true
    onTriggered: {
      var browser = loader.item
      if (loader.status === Loader.Error) {
        root.fail("the snapshot browser failed to load")
        return
      }
      // Stages 72 and 74 race the first listing on purpose, so they run while loading.
      if (!browser || (browser.loading && root.stage !== 72 && root.stage !== 74)) return

      if (root.stage === 0) {
        if (browser.path !== "/home/test" || browser.entries.length !== 4) return
        browser.navigate("/home/test/Documents", "")
        root.stage = 1
        return
      }

      if (root.stage === 1) {
        browser.moveSelection(1)
        if (!browser.selectedEntry || browser.selectedEntry.name !== "notes.md") {
          root.fail("the file was not selectable")
          return
        }
        browser.switchSnapshot(1)
        if (browser.restoreTarget !== null) {
          root.fail("a restore target was offered before the older listing loaded")
          return
        }
        browser.restoreSelected()
        if (fakeService.restoreCalls.length !== 0) {
          root.fail("restoring during a snapshot switch widened the restore: "
            + JSON.stringify(fakeService.restoreCalls))
          return
        }
        root.stage = 2
        return
      }

      if (root.stage === 2) {
        if (!browser.selectedEntry || browser.selectedEntry.name !== "notes.md") {
          root.fail("the selection did not follow the file into the older snapshot")
          return
        }
        browser.restoreSelected()
        if (JSON.stringify(fakeService.restoreCalls)
            !== JSON.stringify([["aaaaaaaa", "/home/test/Documents/notes.md", "file"]])) {
          root.fail("the file was not restored from the older snapshot")
          return
        }
        browser.switchSnapshot(-1)
        var keep = {}
        keep[browser.listingKey(browser.path)] = browser.listings[browser.listingKey(browser.path)]
        browser.listings = keep
        browser.switchSnapshot(1)
        browser.switchSnapshot(-1)
        browser.restoreSelected()
        browser.restoreFolder()
        if (JSON.stringify(fakeService.restoreCalls.slice(1)) !== JSON.stringify([
            ["bbbbbbbb", "/home/test/Documents/notes.md", "file"],
            ["bbbbbbbb", "/home/test/Documents", "dir"]])) {
          root.fail("a quick switch back widened the file restore or mixed it with the folder: "
            + JSON.stringify(fakeService.restoreCalls))
          return
        }
        root.stage = 21
        return
      }

      if (root.stage === 21) {
        if (!browser.selectedEntry || browser.selectedEntry.name !== "notes.md") {
          root.fail("a late older listing disturbed the selection")
          return
        }
        browser.navigate("/home/test/Empty", "")
        root.stage = 22
        return
      }

      if (root.stage === 22) {
        if (!browser.listingReady || !browser.folderRestorable) {
          root.fail("an existing empty folder could not be restored")
          return
        }
        browser.navigate("/home/test/Gone", "")
        root.stage = 23
        return
      }

      if (root.stage === 23) {
        if (!browser.listingReady || browser.folderRestorable) {
          root.fail("a folder missing from the snapshot was offered for restore")
          return
        }
        fakeService.restoreCalls = fakeService.restoreCalls.slice(0, 1)
        browser.switchSnapshot(1)
        browser.navigate("/home/test/Documents", "")
        root.stage = 24
        return
      }

      if (root.stage === 24) {
        browser.switchSnapshot(-1)
        browser.navigate("/home/test/Pictures", "")
        root.stage = 3
        return
      }

      if (root.stage === 3) {
        if (browser.snapshotIndex !== 0 || browser.path !== "/home/test/Pictures") {
          root.fail("the browser did not stay on the newer snapshot's folder")
          return
        }
        browser.switchSnapshot(1)
        browser.switchSnapshot(-1)
        root.stage = 4
        return
      }

      if (root.stage === 4) {
        if (browser.error !== "" || browser.entries.length !== 1 || browser.snapshotIndex !== 0) {
          root.fail("a stale error from the older snapshot covered the current listing: " + browser.error)
          return
        }
        browser.switchSnapshot(1)
        root.stage = 5
        return
      }

      if (root.stage === 5) {
        if (browser.error.indexOf("not found") === -1) {
          root.fail("the current listing's own error was not shown")
          return
        }
        browser.open({ id: "single", name: "Single file" })
        root.stage = 6
        return
      }

      if (root.stage === 6) {
        if (!browser.listingReady) return
        if (browser.path !== "/home/test" || !browser.selectedEntry || browser.selectedEntry.name !== "config.toml") {
          root.fail("a single-file snapshot did not open at its folder with the file selected: " + browser.path)
          return
        }
        browser.open({ id: "home", name: "Home" })
        root.stage = 61
        return
      }

      if (root.stage === 61) {
        if (!browser.listingReady) return
        browser.navigate("/home/test/Swap", "")
        root.stage = 7
        return
      }

      if (root.stage === 7) {
        browser.moveSelection(1)
        browser.switchSnapshot(1)
        root.stage = 71
        return
      }

      if (root.stage === 71) {
        if (browser.path !== "/home/test/Swap" || browser.selectedName !== "inside.txt"
            || browser.restoreTarget !== null || browser.folderRestorable) {
          root.fail("a folder that is a symlink in another snapshot redirected the selection: "
            + browser.path + " " + browser.selectedName)
          return
        }
        browser.open({ id: "swaproot", name: "Swap root" })
        root.stage = 72
        return
      }

      if (root.stage === 72) {
        if (browser.snapshots.length === 0) return
        if (browser.listingReady) {
          root.fail("the slow first listing finished before the switch could race it")
          return
        }
        browser.switchSnapshot(1)
        root.stage = 73
        return
      }

      if (root.stage === 73) {
        if (!browser.listingReady) return
        if (browser.path !== "/home/test/Swap" || browser.selectedName !== ""
            || browser.restoreTarget !== null || browser.folderRestorable) {
          root.fail("switching during the first listing still redirected to the parent: "
            + browser.path + " " + browser.selectedName)
          return
        }
        browser.open({ id: "linkroot", name: "Link root" })
        root.stage = 74
        return
      }

      if (root.stage === 74) {
        if (browser.snapshots.length === 0) return
        browser.switchSnapshot(1)
        root.stage = 75
        return
      }

      if (root.stage === 75) {
        if (!browser.listingReady) return
        browser.moveSelection(1)
        browser.switchSnapshot(-1)
        root.stage = 76
        return
      }

      if (root.stage === 76) {
        if (!browser.listingReady) return
        if (browser.path !== "/home/test/Swap" || browser.selectedName !== "inside.txt"
            || browser.restoreTarget !== null || browser.folderRestorable) {
          root.fail("switching back to the newest snapshot redirected the selection: "
            + browser.path + " " + browser.selectedName)
          return
        }
        root.stage = 8
        return
      }

      console.log("browser tests passed")
      root.finished = true
      stop()
      Qt.quit()
    }
  }

  Timer {
    interval: 10000
    running: true
    repeat: false
    onTriggered: if (!root.finished) root.fail("browser test timed out at stage " + root.stage)
  }
}
