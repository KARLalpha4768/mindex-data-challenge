import { GoogleGenAI } from '@google/genai';
import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

export async function POST(req: Request) {
  try {
    const { message } = await req.json();

    const readmePath = path.join(process.cwd(), '../README.md');
    const readmeContent = fs.readFileSync(readmePath, 'utf-8');

    const systemInstruction = `You are an expert technical assistant representing Karl David's MINDEX Data Engineer Code Challenge submission.
Your answers MUST be strictly based on the technical details, architecture decisions, defect catalogs, and metric definitions found in the provided README document.

<README_DOCUMENT>
${readmeContent}
</README_DOCUMENT>`;

    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: message,
      config: {
        systemInstruction,
        temperature: 0.2,
      },
    });

    return NextResponse.json({ text: response.text });
  } catch (error) {
    console.error('Chatbot API Error:', error);
    return NextResponse.json({ error: 'Failed to generate response' }, { status: 500 });
  }
}