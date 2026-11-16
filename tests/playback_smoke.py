"""Play the production DASH manifest with the application's dash.js build."""
import argparse
import os

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_url')
    parser.add_argument('video_id')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PYTR_SMOKE_BROWSER'),
            args=['--autoplay-policy=no-user-gesture-required'],
        )
        try:
            page = browser.new_page()
            page.goto(base, wait_until='domcontentloaded')
            # Use the same library build as the app, including its WebM support.
            dash_src = page.locator('script[src*="dashjs"]').get_attribute('src')
            if not dash_src:
                raise RuntimeError('Application has no dash.js script')
            page.set_content('<video id="video" muted autoplay></video>')
            page.add_script_tag(url=dash_src)
            page.evaluate('''url => {
                window.playbackErrors = [];
                const player = dashjs.MediaPlayer().create();
                window.smokePlayer = player;
                player.on(dashjs.MediaPlayer.events.ERROR, event => {
                    window.playbackErrors.push({
                        code: event.error && event.error.code,
                        message: event.error && event.error.message,
                    });
                });
                player.initialize(document.getElementById('video'), url, true);
            }''', f'{base}/api/dash/{args.video_id}')
            try:
                page.wait_for_function('''() => {
                    const video = document.getElementById('video');
                    return video.currentTime >= 5 && video.videoWidth > 0
                        && video.getVideoPlaybackQuality().totalVideoFrames > 0;
                }''', timeout=60_000)
                print('DASH browser playback passed: decoded 5 seconds', flush=True)
                # Existing cap detection probes around 90 seconds. Verify that
                # playback also survives a seek beyond the one-minute cap.
                page.evaluate('document.getElementById("video").currentTime = 90')
                page.wait_for_function('''() => {
                    const video = document.getElementById('video');
                    return video.currentTime >= 93 && !video.paused
                        && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA;
                }''', timeout=60_000)
                print('DASH browser seek passed: played beyond 90 seconds', flush=True)
            except Exception:
                print(page.evaluate('''() => {
                    const video = document.getElementById('video');
                    return {time: video.currentTime, readyState: video.readyState,
                        mediaError: video.error && video.error.message,
                        dashErrors: window.playbackErrors};
                }'''), flush=True)
                raise
        finally:
            browser.close()


if __name__ == '__main__':
    main()
