import { GoogleGenAI } from '@google/genai';
import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

export async function POST(req: Request) {
  try {
    const { message } = await req.json();

    const readmePath = path.join(process.cwd(), '../README.md');
    let readmeContent = '';
    try {
      readmeContent = fs.readFileSync(readmePath, 'utf-8');
    } catch {
      readmeContent = 'MINDEX Data Engineer Code Challenge Repository Context';
    }

    const systemInstruction = `You are an expert technical assistant representing Karl David's MINDEX Data Engineer Code Challenge submission.
Your answers MUST be strictly based on the technical details, architecture decisions, defect catalogs, and metric definitions found in the provided README document.

<README_DOCUMENT>
${readmeContent}
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
