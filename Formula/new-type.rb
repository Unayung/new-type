class NewType < Formula
  desc "Local-first voice dictation for macOS (Whisper + Fn/Globe hotkey)"
  homepage "https://github.com/Unayung/new-type"
  url "https://github.com/Unayung/new-type/archive/refs/heads/master.tar.gz"
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

    (bin/"new-type").write <<~EOS
      #!/bin/bash
      exec "#{Formula["uv"].opt_bin}/uv" run --project "#{libexec}" "#{libexec}/main.py" "$@"
    EOS
  end

  service do
    run [opt_bin/"new-type", "daemon"]
    keep_alive true
    log_path "/tmp/new-type.log"
    error_log_path "/tmp/new-type.log"
  end

  def caveats
    <<~EOS
      Grant Accessibility permission to the terminal running new-type:
        System Settings → Privacy & Security → Accessibility

      On first transcription, the Whisper model (~800MB) will download automatically.

      Start the daemon now (and auto-start on login):
        brew services start new-type

      Or run manually:
        new-type daemon
    EOS
  end
end
