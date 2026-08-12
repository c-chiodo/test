import type { Page } from "../App";

export default function Landing({ go }: { go: (p: Page) => void }) {
  return (
    <>
      <header className="hero container">
        <div className="kicker">Soybean oil intelligence</div>
        <h1>
          Predict the oil in every bean — before it's harvested.
        </h1>
        <p className="lede">
          OleoCast turns public weather data into in-season forecasts of
          soybean yield, seed oil concentration, and fatty-acid quality — so
          processors, originators, and growers can plan crush, contracts, and
          logistics around what the crop will actually deliver.
        </p>
        <div className="hero-ctas">
          <button className="btn" onClick={() => go("platform")}>
            Explore the live platform
          </button>
          <button className="btn ghost" onClick={() => go("science")}>
            See the validation
          </button>
        </div>

        <div className="statband">
          <div className="stat">
            <div className="v">R5 → R6</div>
            <div className="l">The oil-critical window</div>
            <div className="sub">
              Seed-fill weather sets oil concentration — we model it stage by
              stage, not by calendar month.
            </div>
          </div>
          <div className="stat">
            <div className="v">80 / 90%</div>
            <div className="l">Calibrated prediction intervals</div>
            <div className="sub">
              Conformal intervals validated leave-one-year-out — coverage you
              can check, not marketing.
            </div>
          </div>
          <div className="stat">
            <div className="v">100%</div>
            <div className="l">Public data inputs</div>
            <div className="sub">
              NASA POWER, Open-Meteo/ERA5, USDA NASS, SSURGO soils — no
              proprietary lock-in.
            </div>
          </div>
          <div className="stat">
            <div className="v">$ / bu</div>
            <div className="l">Composition → crush value</div>
            <div className="sub">
              Every forecast lands as estimated processing value with your own
              price deck.
            </div>
          </div>
        </div>
      </header>

      <section className="section container">
        <h2>Built for the people who turn beans into oil</h2>
        <p className="sub">
          A bushel is not a bushel. Two fields with identical yield can differ
          by a full point of oil — worth real money at the crush plant.
          OleoCast makes that difference visible months early.
        </p>
        <div className="cards">
          <div className="card">
            <span className="chip">Processors &amp; crushers</span>
            <h3>Plan crush around composition</h3>
            <p>
              Forecast oil and protein by supply region to steer procurement,
              blending, and plant scheduling toward the highest estimated
              processing value per bushel.
            </p>
          </div>
          <div className="card">
            <span className="chip">Originators &amp; merchandisers</span>
            <h3>Price what the crop will be</h3>
            <p>
              In-season yield and oil forecasts with honest uncertainty bands
              support basis decisions, quality premiums, and origination
              targeting before harvest confirms them.
            </p>
          </div>
          <div className="card">
            <span className="chip">Growers &amp; agronomists</span>
            <h3>See the season's trajectory</h3>
            <p>
              Track growth stages predicted from actual weather, understand
              which stresses are moving yield and oil, and test what the rest
              of the season could do.
            </p>
          </div>
        </div>
      </section>

      <section className="section container">
        <h2>How it works</h2>
        <p className="sub">
          A transparent pipeline from public weather to processing value — no
          black boxes between you and the forecast.
        </p>
        <div className="steps">
          <div className="step">
            <h3>Ingest public weather</h3>
            <p>
              Daily temperature, rain, sunlight, humidity, and reference ET
              from NASA POWER and Open-Meteo/ERA5, with soil water capacity
              from USDA SSURGO.
            </p>
          </div>
          <div className="step">
            <h3>Date the growth stages</h3>
            <p>
              A degree-day + photoperiod phenology model dates VE through R8
              for each field's maturity group — so features align to the
              biology, not the calendar.
            </p>
          </div>
          <div className="step">
            <h3>Predict with ML + intervals</h3>
            <p>
              Gradient-boosted models predict yield, oil %, protein %, and
              fatty acids from stage-window weather features, with conformal
              prediction intervals sized by how much season remains.
            </p>
          </div>
          <div className="step">
            <h3>Land it in dollars</h3>
            <p>
              Composition flows into crush arithmetic — oil and meal per
              bushel, quality premiums and discounts, estimated processing
              value per acre.
            </p>
          </div>
        </div>
      </section>

      <section className="section container">
        <h2>Honest by design</h2>
        <p className="sub">
          Agricultural ML is easy to oversell. We publish what most vendors
          hide.
        </p>
        <div className="cards">
          <div className="card">
            <h3>Validation you can audit</h3>
            <p>
              Every model ships with leave-one-year-out metrics against
              baselines that must be beaten — a climatological mean and a
              region-mean-plus-trend model. Skill is reported relative to
              those, per target, on the Science page.
            </p>
          </div>
          <div className="card">
            <h3>Uncertainty that means something</h3>
            <p>
              Intervals are conformally calibrated on held-out years and widen
              when less of the season has been observed. When we say 90%, we
              show the measured coverage next to it.
            </p>
          </div>
          <div className="card">
            <h3>Provenance on every number</h3>
            <p>
              Each forecast is stamped with its weather source — live API or
              climatology simulation — and the model card states exactly what
              the models were trained on, including current limitations.
            </p>
          </div>
        </div>
      </section>

      <div className="container">
        <div className="cta-banner">
          <h2>See your supply shed's oil forecast</h2>
          <p>
            The demo runs on eight Corn Belt counties. A partnership pilot
            extends it to your draw territory, your fields, and your price
            deck.
          </p>
          <button className="btn" onClick={() => go("platform")}>
            Launch the demo
          </button>
        </div>
      </div>
    </>
  );
}
