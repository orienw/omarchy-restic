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
      if (!browser || browser.loading) return

      if (root.stage === 0) {
        if (browser.path !== "/home/test" || browser.entries.length !== 3) return
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

      if (browser.error.indexOf("not found") === -1) {
        root.fail("the current listing's own error was not shown")
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
