'use client';

import { useState } from 'react';

export default function ContactPage() {
  const [form, setForm] = useState({ name: '', email: '', message: '' });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setForm({ ...form, [e.target.name]: e.target.value });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    alert('Thanks for contacting us!');
    setForm({ name: '', email: '', message: '' });
  };

  return (
    <div className="bg-white text-gray-800 px-4 py-20">
      <div className="max-w-xl mx-auto space-y-10">
        <div className="text-center">
          <h1 className="text-5xl font-bold font-serif mb-4">Contact Us</h1>
          <p className="text-gray-600 font-sans text-base">
            We'd love to hear from you. Whether you're an investor, partner, or curious reader — drop us a message.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-6 font-sans">
          <input
            name="name"
            type="text"
            placeholder="Your Name"
            value={form.name}
            onChange={handleChange}
            required
            className="w-full border px-4 py-2 rounded text-base"
          />
          <input
            name="email"
            type="email"
            placeholder="Your Email"
            value={form.email}
            onChange={handleChange}
            required
            className="w-full border px-4 py-2 rounded text-base"
          />
          <textarea
            name="message"
            placeholder="Your Message"
            value={form.message}
            onChange={handleChange}
            required
            className="w-full border px-4 py-2 rounded h-32 text-base"
          />
          <button type="submit" className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 transition">
            Submit
          </button>
        </form>
      </div>
    </div>
  );
}
