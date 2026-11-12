/* Rice Racing credits are bundled with the app; only voluntary external links use the network. */
(() => {
  'use strict';
  const panel = document.getElementById('view-credits');
  if (!panel) return;
  panel.setAttribute('aria-labelledby', 'creditsTitle');
  panel.innerHTML = `
    <div class="rr-credits">
      <section class="rr-hero">
        <div class="rr-intro">
          <span class="rr-kicker"><span class="rr-kicker-dot" aria-hidden="true"></span>THE TEAM BEHIND ACCC TELEMETRY</span>
          <h1 id="creditsTitle">We are<br><span>Rice Racing.</span></h1>
          <p class="rr-lead">Students. Engineers. Racing enthusiasts.</p>
          <p>We’re Rice University’s Formula SAE team: a group of college students designing and building a formula race car to compete in FSAE.</p>
          <div class="rr-intro-links">
            <a class="rr-link" href="https://www.riceracing.org/" target="_blank" rel="noopener noreferrer">Meet Rice Racing <span aria-hidden="true">↗</span></a>
            <a class="rr-link" href="#rice-racing-giving" id="creditsGiveJump">Support the team <span aria-hidden="true">↓</span></a>
          </div>
        </div>
        <figure class="rr-team-photo">
          <img src="/static/media/rice-racing-team.jpg" width="1170" height="878" alt="Rice Racing students together at the first general body meeting of the 2026–27 school year" decoding="async">
          <figcaption><span>ONE TEAM. A SHARED AMBITION.</span><span>Rice University · Houston, Texas</span></figcaption>
        </figure>
      </section>

      <section class="rr-mission" aria-labelledby="creditsMissionTitle">
        <div class="rr-mission-mark"><img src="/static/brand/accc-mark.svg" width="88" height="88" alt="ACCC Telemetry: A inside three nested Cs"></div>
        <div>
          <span class="rr-kicker">WHY WE BUILT THIS</span>
          <h2 id="creditsMissionTitle">Every driver deserves a way to improve.</h2>
          <p>Console players should be able to understand their driving and learn from the best players’ inputs. ACCC Telemetry helps you review recorded ACC laps, study your braking and throttle, and compare your inputs with a strong reference lap. Learn after the drive, at your own pace.</p>
        </div>
      </section>

      <section class="rr-giving" id="rice-racing-giving" aria-labelledby="creditsGivingTitle" tabindex="-1">
        <div class="rr-giving-story">
          <span class="rr-kicker">HELP BUILD WHAT COMES NEXT</span>
          <h2 id="creditsGivingTitle">Put your support<br>on the starting grid.</h2>
          <p>A Formula SAE car brings classroom ideas into the real world. Your gift supports Rice Racing’s work to design, build, test, and compete—and the students learning through every step.</p>
          <p>If ACCC Telemetry helps you find your next improvement, consider helping our team take its next step.</p>
          <div class="rr-impact" aria-label="Our work"><span>DESIGN</span><span>BUILD</span><span>COMPETE</span></div>
          <p class="rr-optional">ACCC Telemetry is free to use.<br>Giving is entirely optional.</p>
        </div>
        <div class="rr-giving-card">
          <div class="rr-giving-card-heading"><h3>How to give</h3><span>VIA RICE UNIVERSITY</span></div>
          <ol class="rr-steps">
            <li><span class="rr-step-number" aria-hidden="true">01</span><div><h4>Open Rice’s giving website</h4><p>Go to <a href="https://giving.rice.edu/" target="_blank" rel="noopener noreferrer">giving.rice.edu</a> and click <strong>“Make a Gift”</strong>.</p></div></li>
            <li><span class="rr-step-number" aria-hidden="true">02</span><div><h4>Choose our team</h4><p>Where it says <strong>“Please select an area of Rice to support”</strong>, select <strong>“Other”</strong> and enter our official name:</p></div></li>
          </ol>
          <div class="rr-club-name">
            <span class="rr-club-label">OFFICIAL GIFT DESIGNATION</span>
            <div><strong id="riceRacingClubName">Rice Formula SAE Club</strong><button type="button" id="copyRiceRacingName" aria-label="Copy Rice Formula SAE Club">Copy name</button></div>
          </div>
          <p class="rr-copy-status" id="riceRacingCopyStatus" role="status" aria-live="polite"></p>
          <a class="rr-donate" href="https://giving.rice.edu/" target="_blank" rel="noopener noreferrer">Support Rice Racing <span aria-hidden="true">↗</span></a>
          <p class="rr-giving-note">Opens Rice University’s giving website in your browser. Choose <strong>Other → Rice Formula SAE Club</strong> so your gift supports our team.</p>
        </div>
      </section>

      <section class="rr-gallery" aria-label="Meet the team beyond the simulator">
        <figure>
          <img src="/static/media/rice-racing-workshop.webp" width="1200" height="800" alt="Rice Racing students learning about a Formula SAE car during a visit to UTA Racing’s workshop" loading="lazy" decoding="async">
          <figcaption><strong>Learning from the paddock.</strong><span>Our visit to UTA Racing’s workshop.</span></figcaption>
        </figure>
        <figure>
          <img src="/static/media/rice-racing-hennessey.webp" width="1200" height="931" alt="Rice Racing students gathered around a green road car during a visit to Hennessey Performance" loading="lazy" decoding="async">
          <figcaption><strong>A shared love of performance.</strong><span>Our team visiting Hennessey Performance.</span></figcaption>
        </figure>
      </section>

      <footer class="rr-footer">
        <div><strong>ACCC Telemetry</strong><span>ACC Console Telemetry · Made by Rice Racing</span></div>
        <div class="rr-footer-links"><a href="https://www.riceracing.org/" target="_blank" rel="noopener noreferrer">Team website ↗</a><span>Photos: <a href="https://www.riceracing.org/" target="_blank" rel="noopener noreferrer">Rice Racing</a></span></div>
      </footer>
    </div>`;

  panel.querySelector('#creditsGiveJump').addEventListener('click', event => {
    event.preventDefault();
    const giving = panel.querySelector('#rice-racing-giving');
    giving.focus({preventScroll: true});
    giving.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
  });

  panel.querySelector('#copyRiceRacingName').addEventListener('click', async () => {
    const status = panel.querySelector('#riceRacingCopyStatus');
    try {
      await navigator.clipboard.writeText('Rice Formula SAE Club');
      status.textContent = 'Copied. Select Other on Rice’s giving form, then paste this name.';
    } catch (_) {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(panel.querySelector('#riceRacingClubName'));
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Name selected. Copy it, then paste it under Other on Rice’s giving form.';
    }
  });
})();
