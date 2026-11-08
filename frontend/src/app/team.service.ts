import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

export interface Person { owner: string; name: string; }

const B = '/api/team';

/** People the current user can add to a project team (platform users + people entered by hand). */
@Injectable({ providedIn: 'root' })
export class TeamApi {
  private http = inject(HttpClient);
  directory() { return firstValueFrom(this.http.get<Person[]>(`${B}/directory`)); }
}
