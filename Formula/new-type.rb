class NewType < Formula
  desc "Local-first voice dictation for macOS (Whisper + Fn/Globe hotkey)"
  homepage "https://github.com/Unayung/new-type"
  url "https://github.com/Unayung/new-type/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "6469adabe2bbaccd3e2b42a14307d825e79db0a69719e69a4d35087cadcec121"
  version "0.1.0"
  license "MIT"
  head "https://github.com/Unayung/new-type.git", branch: "master"

  depends_on "uv"
  depends_on :macos

  def install
    libexec.install Dir["*"]
    cd libexec do
      system Formula["uv"].opt_bin/"uv", "sync", "--frozen", "--no-dev"
    end

    # CLI wrapper
    (bin/"new-type").write <<~EOS
      #!/bin/bash
      exec "#{Formula["uv"].opt_bin}/uv" run --project "#{libexec}" "#{libexec}/main.py" "$@"
    EOS

    # .app bundle — gives macOS a bundle ID to grant Accessibility permission to
    app = prefix/"new-type.app/Contents"
    (app/"MacOS").mkpath
    (app/"MacOS/new-type").write <<~EOS
      #!/bin/bash
      exec "#{bin}/new-type" daemon
    EOS
    chmod 0755, app/"MacOS/new-type"
    (app/"Info.plist").write <<~EOS
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0">
      <dict>
        <key>CFBundleIdentifier</key>   <string>com.unayung.new-type</string>
        <key>CFBundleName</key>         <string>new-type</string>
        <key>CFBundleExecutable</key>   <string>new-type</string>
        <key>CFBundleVersion</key>      <string>#{version}</string>
        <key>LSUIElement</key>          <true/>
      </dict>
      </plist>
    EOS
  end

  service do
    run [opt_prefix/"new-type.app/Contents/MacOS/new-type"]
    keep_alive true
    log_path "/tmp/new-type.log"
    error_log_path "/tmp/new-type.log"
  end

  def caveats
    <<~EOS
      Grant Accessibility permission to the app bundle:
        System Settings → Privacy & Security → Accessibility → add:
        #{opt_prefix}/new-type.app

      On first transcription, the Whisper model (~800MB) will download automatically.

      Start the daemon (and auto-start on login):
        brew services start unayung/new-type/new-type
    EOS
  end
end
