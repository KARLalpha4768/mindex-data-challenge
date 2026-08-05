import { GoogleGenAI } from '@google/genai';
import { NextResponse } from 'next/server';
import { README_CONTEXT } from '@/lib/readmeContext';

export const dynamic = 'force-dynamic';

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

export async function POST(req: Request) {
  try {
    const { message } = await req.json();

    const systemInstruction = `You are an expert technical assistant representing Karl David's MINDEX Data Engineer Code Challenge submission.
Your answers MUST be strictly based on the technical details, architecture decisions, defect catalogs, and metric definitions provided.

<README_DOCUMENT>
${README_CONTEXT}
</README_DOCUMENT>`;

    const streamResult = await ai.models.generateContentStream({
      model: 'gemini-2.5-flash',
      contents: message,
      config: {
        systemInstruction,
        temperature: 0.2,
      },
    });

    const encoder = new TextEncoder();
    const readableStream = new ReadableStream({
      async start(controller) {
        for await (const chunk of streamResult) {
          if (chunk.text) {
            controller.enqueue(encoder.encode(chunk.text));
          }
        }
        controller.close();
      },
    });

    return new Response(readableStream, {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Transfer-Encoding': 'chunked',
      },
    });
  } catch (error) {
    console.error('Chatbot Streaming API Error:', error);
    return NextResponse.json({ error: 'Failed to generate response' }, { status: 500 });
  }
}