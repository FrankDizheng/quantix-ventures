export default function HomePage() {
  return (
    <div className="bg-white text-gray-800">
      {/* Hero Section */}
      <section
        className="relative h-[500px] bg-cover bg-center bg-no-repeat"
        style={{ backgroundImage: "url('/hero-bg.jpg')" }}
      >
        <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center text-white text-center px-4">
          <h1 className="text-4xl md:text-5xl font-bold mb-3 drop-shadow-lg">Quantix Ventures</h1>
          <p className="text-lg max-w-xl drop-shadow-sm">
            Fresh perspectives on today’s financial markets
          </p>
        </div>
      </section>


      {/* About / Intro */}
      <section className="max-w-5xl mx-auto py-16 px-6">
        <h2 className="text-3xl font-bold text-center mb-6">Who We Are</h2>
        <p className="text-center text-gray-700 max-w-3xl mx-auto leading-relaxed">
          Quantix Ventures is a dynamic trading and consulting firm focused on delivering value across global
          commodity markets. Our strategies combine industry expertise, smart data, and seamless logistics to
          empower businesses around the world.
        </p>
      </section>

      {/* Services */}
      <section className="py-20 bg-white text-center">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="text-3xl font-semibold mb-12">Learn about our services</h2>
          <div className="grid md:grid-cols-3 gap-8 text-left">
            <div>
              <h3 className="text-lg font-bold mb-2">Analysis on the current financial markets</h3>
              <p className="text-gray-600 text-sm">
                Receive daily updates and in-depth explanations into what can move and why the markets react.
              </p>
            </div>
            <div>
              <h3 className="text-lg font-bold mb-2">Technical, fundamental and sentiment perspectives</h3>
              <p className="text-gray-600 text-sm">
                Strategies based on a combination of proven methodologies and real-time insights.
              </p>
            </div>
            <div>
              <h3 className="text-lg font-bold mb-2">COT and RTP data</h3>
              <p className="text-gray-600 text-sm">
                Key data behind market activity, presented with actionable analysis every week.
              </p>
            </div>
          </div>
        </div>
      </section>


      {/* CTA */}
      <section className="text-center py-16 px-6">
        <h2 className="text-2xl font-bold mb-3">Work With Us</h2>
        <p className="text-gray-600 mb-6">
          Ready to take the next step in expanding your trade capacity? Reach out and let's build something global.
        </p>
        <a
          href="/contact"
          className="inline-block bg-blue-600 text-white px-6 py-3 rounded hover:bg-blue-700 transition"
        >
          Contact Us
        </a>
      </section>
    </div>
  );
}
